"""
大模型适配器抽象层（M9.1/M9.4/M9.6/M9.7）

整体思路：
    以统一接口屏蔽厂商差异，提供 health/embed/chat/chat_stream 能力，供 M9 健康检查、
    M3 嵌入、M7 对话、M8 续写复用；运行时按 config 动态构建，无需重启即可切换（M9.4）。

关键点：
    1. 双实现：OllamaAdapter（本地，无需 api_key）+ OpenAIAdapter（OpenAI 兼容协议，
       覆盖 OpenAI/Claude/智谱/通义/DeepSeek 等）。
    2. 全部基于 httpx.AsyncClient 异步调用，超时取 config.timeout。
    3. chat_stream 为异步生成器逐 token 产出，供 SSE 流式（M7.5/M8）。

实现逻辑：
    get_adapter 工厂按 provider 选择实现；各方法直连厂商 HTTP 端点并解析响应。
"""
import json
import time

import httpx

# 走 OpenAI 兼容协议的厂商（其余非 ollama 默认也按兼容协议处理）
_OPENAI_COMPATIBLE = {
    "openai", "claude", "anthropic", "智谱", "zhipu", "通义", "qwen",
    "dashscope", "deepseek", "moonshot", "kimi", "compatible",
}


class LLMAdapter:
    """适配器抽象：定义统一的探活/嵌入/对话接口。"""

    async def health(self) -> tuple[bool, int, str]:
        """探活：返回 (是否连通, 延迟毫秒, 明细)。"""
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入：返回与输入等长的向量列表。"""
        raise NotImplementedError

    async def chat(self, messages: list[dict], **opts) -> str:
        """一次性对话：返回完整回复文本。"""
        raise NotImplementedError

    async def chat_stream(self, messages: list[dict], **opts):
        """流式对话：异步逐段产出回复文本。"""
        raise NotImplementedError


class OpenAIAdapter(LLMAdapter):
    """OpenAI 兼容协议适配器（/v1/models、/v1/embeddings、/v1/chat/completions）。"""

    def __init__(self, cfg, api_key: str | None):
        self.base_url = (cfg.base_url or "https://api.openai.com").rstrip("/")
        self.api_key = api_key
        self.model = cfg.model
        self.timeout = cfg.timeout or 60

    def _headers(self) -> dict:
        """构造请求头，附带 Bearer 鉴权。"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health(self) -> tuple[bool, int, str]:
        """GET /v1/models 探活并计时。"""
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
        latency = int((time.perf_counter() - t0) * 1000)
        return resp.status_code < 400, latency, f"HTTP {resp.status_code}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """POST /v1/embeddings 批量嵌入。"""
        payload = {"model": self.model, "input": texts}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/v1/embeddings", headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]

    async def chat(self, messages: list[dict], **opts) -> str:
        """POST /v1/chat/completions 一次性对话。"""
        payload = {"model": self.model, "messages": messages, "stream": False, **opts}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/v1/chat/completions", headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(self, messages: list[dict], **opts):
        """POST /v1/chat/completions（stream）逐 token 产出。"""
        payload = {"model": self.model, "messages": messages, "stream": True, **opts}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                     headers=self._headers(), json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta


class OllamaAdapter(LLMAdapter):
    """Ollama 本地适配器（/api/tags、/api/embeddings、/api/chat），无需 api_key。"""

    def __init__(self, cfg, api_key: str | None = None):
        self.base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
        self.model = cfg.model
        self.timeout = cfg.timeout or 60

    async def health(self) -> tuple[bool, int, str]:
        """GET /api/tags 探活并计时。"""
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
        latency = int((time.perf_counter() - t0) * 1000)
        return resp.status_code < 400, latency, f"HTTP {resp.status_code}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """逐条 POST /api/embeddings（Ollama 单条嵌入）。"""
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for text in texts:
                resp = await client.post(f"{self.base_url}/api/embeddings",
                                         json={"model": self.model, "prompt": text})
                resp.raise_for_status()
                out.append(resp.json()["embedding"])
        return out

    async def chat(self, messages: list[dict], **opts) -> str:
        """POST /api/chat 一次性对话。"""
        payload = {"model": self.model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def chat_stream(self, messages: list[dict], **opts):
        """POST /api/chat（stream）逐段产出（Ollama 按行返回 JSON）。"""
        payload = {"model": self.model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = obj.get("message", {}).get("content")
                    if piece:
                        yield piece
                    if obj.get("done"):
                        break


def get_adapter(cfg, api_key: str | None) -> LLMAdapter:
    """工厂：按 provider 构建适配器（ollama→本地，其余→OpenAI 兼容）。"""
    provider = (cfg.provider or "").strip().lower()
    if provider == "ollama":
        return OllamaAdapter(cfg, api_key)
    return OpenAIAdapter(cfg, api_key)
