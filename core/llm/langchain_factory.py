"""
LangChain 模型工厂与统一适配器（混合分析管道 M1 / 全面接入 LangChain 调用层）

整体思路：
    本项目所有大模型调用（对话 chat / 流式 chat_stream / 向量嵌入 embed / 健康探测 health）
    统一收敛到 LangChain 的 ChatModel / Embeddings 之上，对外暴露与历史用法完全兼容的
    LangChainAdapter（提供 chat / chat_stream / embed / health / get_last_usage 与
    get_adapter(cfg, api_key) 工厂），从而彻底替换自研 services.llm_adapters，避免双份维护成本。

关键点：
    1. 适配器仅做"薄封装"：异步调用、用量统计、超时、流式聚合；业务语义仍由各 service 决定。
    2. provider 自动分流：openai 兼容走 ChatOpenAI + OpenAIEmbeddings；ollama 走 ChatOllama + OllamaEmbeddings。
    3. api_key 由本层统一从配置解密（get_adapter 透传的 api_key 仅为兼容旧签名，实际以 cfg 内密文为准）。

实现逻辑：
    get_adapter → LangChainAdapter(cfg) → 构造时构建 model 与 embeddings →
    chat / chat_stream 经 model.ainvoke / model.astream 调用并把 usage_metadata
    归一化到 {tokens_in, tokens_out}；embed 经 embeddings.aembed_documents。
"""
import logging
import os
import time

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings

from common import crypto
from common.exceptions import BizError
from models.llm_config import LLMConfig

logger = logging.getLogger(__name__)

CHAT_TIMEOUT = float(os.getenv("LLM_CHAT_TIMEOUT", "240"))


def _norm_base_url(base_url: str) -> str:
    """规范化 base_url：去除尾随斜杠。"""
    if not base_url:
        return ""
    return base_url.rstrip("/")


def _build_openai(cfg: LLMConfig):
    """构造 openai 兼容 ChatOpenAI（含 base_url / api_key 解密 / 超时）。"""
    api_key = crypto.decrypt(cfg.api_key) if cfg.api_key else None
    kwargs = dict(
        model=cfg.model,
        api_key=api_key,
        temperature=getattr(cfg, "temperature", 0.7) or 0.7,
        base_url=_norm_base_url(cfg.base_url) or None,
        timeout=CHAT_TIMEOUT,
        max_retries=2,
    )
    if getattr(cfg, "max_tokens", None):
        kwargs["max_tokens"] = getattr(cfg, "max_tokens", None)
    return ChatOpenAI(**kwargs)


def _build_ollama(cfg: LLMConfig):
    """构造本地 ChatOllama（无需 api_key，max_tokens 映射为 num_predict）。"""
    kwargs = dict(
        model=cfg.model,
        base_url=_norm_base_url(cfg.base_url) or "http://localhost:11434",
        temperature=getattr(cfg, "temperature", 0.7) or 0.7,
        timeout=CHAT_TIMEOUT,
    )
    if getattr(cfg, "max_tokens", None):
        kwargs["num_predict"] = getattr(cfg, "max_tokens", None)
    return ChatOllama(**kwargs)


def get_langchain_model(cfg: LLMConfig):
    """LLMConfig → LangChain 聊天模型，按 provider 自动分流（openai 兼容 / ollama）。"""
    provider = (cfg.provider or "").strip().lower()
    if provider == "ollama":
        return _build_ollama(cfg)
    return _build_openai(cfg)


def _build_embeddings(cfg: LLMConfig):
    """LLMConfig → LangChain Embeddings，与 chat 同源 provider 分流。"""
    provider = (cfg.provider or "").strip().lower()
    api_key = crypto.decrypt(cfg.api_key) if cfg.api_key else None
    base_url = _norm_base_url(cfg.base_url) or None
    if provider == "ollama":
        return OllamaEmbeddings(model=cfg.model, base_url=base_url or "http://localhost:11434")
    return OpenAIEmbeddings(model=cfg.model, api_key=api_key, base_url=base_url)


def _text_of(message) -> str:
    """把 LangChain 的 AIMessage / AIMessageChunk 内容统一成字符串。"""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _extract_usage(message) -> dict | None:
    """从 LangChain 消息的 usage_metadata 归一化出 {tokens_in, tokens_out}。"""
    meta = getattr(message, "usage_metadata", None)
    if not meta:
        return None
    return {
        "tokens_in": int(meta.get("input_tokens") or 0),
        "tokens_out": int(meta.get("output_tokens") or 0),
    }


def _friendly_llm_error(e: Exception) -> "BizError":
    """将底层 LLM 调用异常翻译为可读业务错误。

    典型根因：base_url 非 OpenAI 兼容端点（如缺 /v1 路径）时接口返回 HTML/纯文本，
    被当成 str 响应后，langchain-openai 会触发 'str' object has no attribute 'model_dump'/'choices'。
    """
    msg = str(e)
    if "has no attribute" in msg or "model_dump" in msg or "'str'" in msg or "Expecting value" in msg:
        return BizError(
            502,
            "大模型调用失败：LLM 配置无效。请检查 API Key、base_url 是否为有效的 "
            "OpenAI 兼容端点（通常需以 /v1 结尾），以及模型名称是否可用。",
        )
    if "Connection" in type(e).__name__ or "timeout" in msg.lower():
        return BizError(502, f"大模型调用失败：无法连接服务，请检查 base_url 与网络。{msg}")
    return BizError(502, f"大模型调用失败：{msg}")


class LangChainAdapter:
    """LangChain 实现的大模型适配器，替代旧 services.llm_adapters。

    提供原接口：chat / chat_stream / embed / health / get_last_usage，
    调用方代码（各 service）仅需替换 import 即可无缝切换。
    """

    def __init__(self, cfg: LLMConfig, api_key: str | None = None):
        self.cfg = cfg
        self._model = get_langchain_model(cfg)
        self._embeddings = None
        self._last_usage = None

    def _bind(self, json_mode: bool = False, **opts):
        """把业务侧透传的采样参数（temperature/max_tokens/top_p/stop 等）绑定到模型。

        当 json_mode=True 时，按 provider 施加 JSON 输出约束：
        ollama 走 bind(format="json")；其余（openai 兼容）走 response_format=json_object。
        该约束对 7B 本地模型尤为关键——否则其常返回非 JSON 的乱码/散文。
        """
        filtered = {
            k: v
            for k, v in opts.items()
            if k in ("temperature", "max_tokens", "top_p", "stop", "presence_penalty", "frequency_penalty")
            and v is not None
        }
        provider = (self.cfg.provider or "").strip().lower()
        model = self._model
        if json_mode:
            if provider == "ollama":
                model = model.bind(format="json")
            else:
                model = model.bind(**{"response_format": {"type": "json_object"}})
        if filtered:
            # ollama 用 num_predict 表达 max_tokens，做兼容转换
            if provider == "ollama" and "max_tokens" in filtered:
                filtered["num_predict"] = filtered.pop("max_tokens")
            model = model.bind(**filtered)
        return model

    async def chat(self, messages: list, json_mode: bool = False, **opts) -> str:
        """单次对话调用，返回文本，并把用量写入 _last_usage。"""
        if self._model is None:
            raise BizError(500, "模型未初始化（请检查 LLM 配置的 provider/model 是否受支持）")
        try:
            resp = await self._bind(json_mode=json_mode, **opts).ainvoke(messages)
        except Exception as e:
            raise _friendly_llm_error(e) from e
        self._last_usage = _extract_usage(resp)
        return _text_of(resp)

    async def chat_stream(self, messages: list, json_mode: bool = False, **opts):
        """流式对话，逐块 yield 文本片段，并尽力捕获末块用量。"""
        if self._model is None:
            raise BizError(500, "模型未初始化（请检查 LLM 配置的 provider/model 是否受支持）")
        try:
            async for chunk in self._bind(json_mode=json_mode, **opts).astream(messages):
                text = _text_of(chunk)
                if text:
                    yield text
                usage = _extract_usage(chunk)
                if usage:
                    self._last_usage = usage
        except Exception as e:
            raise _friendly_llm_error(e) from e

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化，返回与输入同序的向量列表。"""
        if self._embeddings is None:
            self._embeddings = _build_embeddings(self.cfg)
        return await self._embeddings.aembed_documents(texts)

    async def health(self) -> tuple[bool, int, str]:
        """健康探测：以一次极简 ainvoke 验证连通性，返回 (是否可用, 延迟ms, 详情)。"""
        t0 = time.perf_counter()
        try:
            await self._model.ainvoke([{"role": "user", "content": "ping"}])
            latency = int((time.perf_counter() - t0) * 1000)
            return True, latency, "ok"
        except Exception as e:  # noqa: BLE001
            latency = int((time.perf_counter() - t0) * 1000)
            return False, latency, str(e)[:200]

    def get_last_usage(self) -> dict | None:
        """返回最近一次调用的归一化用量 {tokens_in, tokens_out}。"""
        return self._last_usage


def get_adapter(cfg: LLMConfig, api_key: str | None = None) -> LangChainAdapter:
    """工厂方法：构造 LangChain 适配器。

    参数 api_key 仅为兼容旧签名（旧代码先 crypto.decrypt 再传入），本层实际以 cfg 内密文为准，
    故透传值不参与构造。
    """
    return LangChainAdapter(cfg, api_key)


async def invoke_with_usage(model, prompt_value, *, owner_id, task_type, config_id=None):
    """执行调用并回写用量（复用现有 record_llm_usage），供需要即时记录用量的场景使用。"""
    resp = await model.ainvoke(prompt_value)
    meta = getattr(resp, "usage_metadata", None) or {}
    from services.task_service import record_llm_usage

    model_name = None
    rm = getattr(resp, "response_metadata", None)
    if isinstance(rm, dict):
        model_name = rm.get("model_name")
    if not model_name:
        model_name = getattr(model, "model_name", None)

    await record_llm_usage(
        owner_id=owner_id,
        config_id=config_id,
        model=model_name,
        task_type=task_type,
        tokens_in=meta.get("input_tokens", 0),
        tokens_out=meta.get("output_tokens", 0),
    )
    return resp
