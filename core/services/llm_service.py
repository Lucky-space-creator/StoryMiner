"""
大模型配置业务逻辑（M9）

整体思路：
    聚合模型配置 CRUD、设为默认、健康检查与用量统计，密钥全程加密存取，统一返回契约友好的 dict。

关键点：
    1. 所有配置按 owner_id 隔离（读取含全局配置，改删/默认仅限本人）。
    2. api_key 入库前 Fernet 加密，出参隐藏明文仅暴露 has_key。
    3. 健康检查解密密钥→构建适配器→探活，并回写 status(active/error)。

实现逻辑：
    委托 llm_repo 数据访问、crypto 加解密、llm_adapters 适配器；本层只做业务编排与字段映射。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.llm_config import LLMConfigCreate, LLMConfigUpdate
from models.llm_config import LLMConfig
from repositories import llm_repo
from common import crypto
from common.exceptions import BizError
from llm import langchain_factory as llm_adapters


def _iso(dt) -> str | None:
    """时间转 ISO 字符串。"""
    return dt.isoformat() if isinstance(dt, datetime) else None


def config_out(cfg: LLMConfig) -> dict:
    """模型配置出参（隐藏密钥明文）。"""
    return {
        "id": cfg.id, "owner_id": cfg.owner_id, "name": cfg.name,
        "provider": cfg.provider, "model": cfg.model, "base_url": cfg.base_url,
        "has_key": bool(cfg.api_key), "llm_type": cfg.llm_type,
        "is_default": cfg.is_default, "weight": cfg.weight, "timeout": cfg.timeout,
        "status": cfg.status, "extra": cfg.extra or {},
        "created_at": _iso(cfg.created_at), "updated_at": _iso(cfg.updated_at),
    }


async def create_config(session: AsyncSession, owner_id: int, data: LLMConfigCreate) -> dict:
    """新增配置（M9.1/M9.2）：密钥加密存储。"""
    cfg = LLMConfig(
        owner_id=owner_id, name=data.name, provider=data.provider, model=data.model,
        base_url=data.base_url, api_key=crypto.encrypt(data.api_key),
        llm_type=data.llm_type, weight=data.weight, timeout=data.timeout,
        status="active", extra=data.extra or {},
    )
    await llm_repo.create(session, cfg)
    await session.commit()
    await session.refresh(cfg)
    return config_out(cfg)


async def list_configs(session: AsyncSession, owner_id: int, llm_type: str | None) -> list[dict]:
    """配置列表（M9.1，可按 llm_type 过滤）。"""
    items = await llm_repo.list_configs(session, owner_id, llm_type)
    return [config_out(c) for c in items]


async def update_config(session: AsyncSession, owner_id: int, cfg_id: int, data: LLMConfigUpdate) -> dict:
    """更新配置：api_key 传入则加密覆盖，留空不动。"""
    cfg = await llm_repo.get_owned(session, owner_id, cfg_id)
    if not cfg:
        raise BizError(404, "模型配置不存在")
    if data.api_key is not None:
        cfg.api_key = crypto.encrypt(data.api_key)
    for f in ("name", "provider", "model", "base_url", "llm_type", "weight", "timeout", "status", "extra"):
        v = getattr(data, f)
        if v is not None:
            setattr(cfg, f, v)
    await session.commit()
    await session.refresh(cfg)
    return config_out(cfg)


async def delete_config(session: AsyncSession, owner_id: int, cfg_id: int) -> None:
    """删除配置（仅本人）。"""
    cfg = await llm_repo.get_owned(session, owner_id, cfg_id)
    if not cfg:
        raise BizError(404, "模型配置不存在")
    await llm_repo.delete_config(session, cfg)
    await session.commit()


async def set_default(session: AsyncSession, owner_id: int, cfg_id: int) -> dict:
    """设为某类型默认模型（M9.3）：清旧默认后置位。"""
    cfg = await llm_repo.get_owned(session, owner_id, cfg_id)
    if not cfg:
        raise BizError(404, "模型配置不存在")
    await llm_repo.clear_default(session, owner_id, cfg.llm_type)
    cfg.is_default = True
    await session.commit()
    await session.refresh(cfg)
    return config_out(cfg)


async def health(session: AsyncSession, owner_id: int, cfg_id: int) -> dict:
    """健康检查（M9.6）：连通+延迟，回写 status。"""
    cfg = await llm_repo.get_visible(session, owner_id, cfg_id)
    if not cfg:
        raise BizError(404, "模型配置不存在")
    adapter = llm_adapters.get_adapter(cfg, crypto.decrypt(cfg.api_key))
    try:
        ok, latency, detail = await adapter.health()
    except Exception as e:  # 网络/超时等异常统一视为不可用
        ok, latency, detail = False, None, str(e)
    cfg.status = "active" if ok else "error"
    await session.commit()
    return {"id": cfg_id, "ok": ok, "latency_ms": latency, "detail": detail}


async def usage(session: AsyncSession, owner_id: int) -> dict:
    """配额与成本统计（M9.5，对接 M14）。"""
    rows = await llm_repo.usage_stats(session, owner_id)
    items = [{
        "config_id": r.config_id, "model": r.model, "task_type": r.task_type,
        "calls": int(r.calls), "tokens_in": int(r.tokens_in),
        "tokens_out": int(r.tokens_out), "cost": float(r.cost or Decimal(0)),
    } for r in rows]
    total = {
        "calls": sum(i["calls"] for i in items),
        "tokens_in": sum(i["tokens_in"] for i in items),
        "tokens_out": sum(i["tokens_out"] for i in items),
        "cost": round(sum(i["cost"] for i in items), 6),
    }
    return {"items": items, "total": total}
