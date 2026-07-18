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

    def __init__(self):
        self._last_usage: dict | None = None  # 最近一次调用的 Token 用量 {tokens_in, tokens_out}

    def get_last_usage(self) -> dict | None:
        """获取最近一次 chat/embed 调用的 Token 用量，返回 {tokens_in, tokens_out} 或 None。"""
        return self._last_usage

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
        super().__init__()
        self.base_url = (cfg.base_url or "https://api.openai.com").rstrip("/")
        self.api_key = api_key
        self.model = cfg.model
        # 兜底超时 300s：长文分析单次调用可能耗时数分钟，避免默认 60s 直接超时失败
        self.timeout = cfg.timeout or 300

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
        """POST /v1/chat/completions 一次性对话。

        返回格式：str（纯文本），同时内部记录 _last_usage 供上层提取 Token 用量。
        上层可调用 get_last_usage() 获取本次调用的 token 统计。
        """
        payload = {"model": self.model, "messages": messages, "stream": False, **opts}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/v1/chat/completions", headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        # 记录本次调用的 Token 用量，供上层写入 LLMUsage
        usage = data.get("usage", {})
        self._last_usage = {
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }
        content = data["choices"][0]["message"]["content"]
        # 兼容部分 OpenAI 兼容模型将 content 以内容块列表形式返回（多模态/结构化输出场景）
        if isinstance(content, list):
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        return content or ""

    async def chat_stream(self, messages: list[dict], **opts):
        """POST /v1/chat/completions（stream）逐 token 产出，并捕获末段 usage 记录 Token 用量。"""
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, **opts}
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
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    # OpenAI 兼容在末段返回带 usage 的 chunk，捕获供上层统计
                    if obj.get("usage"):
                        self._last_usage = {
                            "tokens_in": obj["usage"].get("prompt_tokens", 0),
                            "tokens_out": obj["usage"].get("completion_tokens", 0),
                        }
                    choices = obj.get("choices")
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield delta


class OllamaAdapter(LLMAdapter):
    """Ollama 本地适配器（/api/tags、/api/embeddings、/api/chat），无需 api_key。"""

    def __init__(self, cfg, api_key: str | None = None):
        super().__init__()
        self.base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
        self.model = cfg.model
        # 兜底超时 300s：本地模型跑长文 prefill+decode 可能数分钟
        self.timeout = cfg.timeout or 300

    async def health(self) -> tuple[bool, int, str]:
        """GET /api/tags 探活并计时。"""
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
        latency = int((time.perf_counter() - t0) * 1000)
        return resp.status_code < 400, latency, f"HTTP {resp.status_code}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量 POST /api/embed（Ollama 批量嵌入，单请求多文本）。

        整体思路：用 /api/embed 一次请求传入 input 列表，替代逐条 /api/embeddings 串行，
            将百万字（上万切片）的嵌入耗时从与切片数成正比降到与批次数成正比。
        关键点：
            1. 返回体为 {"embeddings": [[...], ...]}，与入参顺序一致。
            2. 低版本 Ollama 可能不支持 /api/embed，捕获异常后回退逐条嵌入，保证兼容不退化。
        实现逻辑：优先批量请求；失败则串行兜底。
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(f"{self.base_url}/api/embed",
                                         json={"model": self.model, "input": texts})
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings:
                    return embeddings
            except Exception:
                # 兼容旧版 Ollama：回退逐条 /api/embeddings 串行嵌入
                pass
            out: list[list[float]] = []
            for text in texts:
                resp = await client.post(f"{self.base_url}/api/embeddings",
                                         json={"model": self.model, "prompt": text})
                resp.raise_for_status()
                out.append(resp.json()["embedding"])
            return out

    async def chat(self, messages: list[dict], **opts) -> str:
        """POST /api/chat 一次性对话。

        返回格式：str（纯文本），同时内部记录 _last_usage 供上层提取 Token 用量。
        """
        payload = {"model": self.model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
        data = resp.json()
        # Ollama 返回 eval_count / prompt_eval_count
        self._last_usage = {
            "tokens_in": data.get("prompt_eval_count", 0),
            "tokens_out": data.get("eval_count", 0),
        }
        content = data["message"]["content"]
        # 兼容 content 为内容块列表的情况
        if isinstance(content, list):
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        return content or ""

    async def chat_stream(self, messages: list[dict], **opts):
        """POST /api/chat（stream）逐段产出（Ollama 按行返回 JSON），done 帧记录 Token 用量。"""
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
                    if obj.get("done"):
                        # Ollama 在 done 帧返回 eval_count / prompt_eval_count
                        self._last_usage = {
                            "tokens_in": obj.get("prompt_eval_count", 0),
                            "tokens_out": obj.get("eval_count", 0),
                        }
                        break
                    piece = obj.get("message", {}).get("content")
                    if piece:
                        yield piece


def get_adapter(cfg, api_key: str | None) -> LLMAdapter:
    """工厂：按 provider 构建适配器（ollama→本地，其余→OpenAI 兼容）。"""
    provider = (cfg.provider or "").strip().lower()
    if provider == "ollama":
        return OllamaAdapter(cfg, api_key)
    return OpenAIAdapter(cfg, api_key)
