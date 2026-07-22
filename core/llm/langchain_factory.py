"""
LangChain 模型工厂（混合分析管道 M1 / LangChain 接入第一板块 A）

整体思路：
    用 LangChain 的 ChatOpenAI / ChatOllama 替代自研 llm_adapters 的「单次 LLM 调用」，
    统一管理模型构造、api_key 解密、用量回写；业务层经双轨开关 USE_LANGCHAIN 决定是否走本工厂。

关键点：
    1. 仅负责「执行单次调用」，降级选路仍由 repositories.llm_repo.list_for_dispatch 承担。
    2. api_key 经 common.crypto.decrypt 解密，禁止明文；工厂是唯一解密点。
    3. LangChain 相关 import 延迟到函数内，避免未安装/未启用时拖慢启动或导入失败（呼应风险缓解）。
    4. 用量经 invoke_with_usage 包装，复用 services.task_service.record_llm_usage，零改动统计表。

实现逻辑：
    get_langchain_model(config, **kw)：按 provider 构造 ChatOpenAI / ChatOllama；
    invoke_with_usage(...)：ainvoke + 提取 usage_metadata 回写 story_llm_usage。
"""
from models.llm_config import LLMConfig
from common.crypto import decrypt

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def get_langchain_model(config: LLMConfig, **kw):
    """LLMConfig → LangChain 聊天模型，替代 llm_adapters.get_adapter。

    局部延迟导入 LangChain，避免全局加载；未安装 LangChain 时不触发 ImportError。
    provider == 'ollama' → ChatOllama（本地，无需 api_key）；其余 → ChatOpenAI 兼容协议。
    """

    temperature = kw.get("temperature", 0.3)
    timeout = config.timeout or 300
    provider = (config.provider or "").strip().lower()

    if provider == "ollama":
        return ChatOllama(
            base_url=config.base_url or "http://localhost:11434",
            model=config.model,
            temperature=temperature,
            timeout=timeout,
        )

    api_key = decrypt(config.api_key) if config.api_key else None
    return ChatOpenAI(
        base_url=config.base_url,
        api_key=api_key,
        model=config.model,
        temperature=temperature,
        timeout=timeout,
        max_retries=kw.get("max_retries", 2),
    )


async def invoke_with_usage(model, prompt_value, *, owner_id, task_type, config_id=None):
    """执行调用并回写用量（复用现有 record_llm_usage）。

    LangChain 的 AIMessage.usage_metadata 提供 {input_tokens, output_tokens}，
    包装为与旧 adapter 一致的 story_llm_usage 记录，零改动现有统计/仪表盘。
    """
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
