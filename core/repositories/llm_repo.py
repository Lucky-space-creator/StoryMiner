"""
大模型配置数据访问（M9）

整体思路：
    封装 LLMConfig 的读写与「设为默认」互斥、降级分发查询，以及 LLMUsage 用量聚合，
    所有可见性遵循「本人配置 + 全局配置(owner_id IS NULL)」规则，变更仅限本人配置。

关键点：
    1. get_visible 返回本人或全局配置（供读取/健康检查）；get_owned 仅返回本人配置（供改删/默认）。
    2. clear_default 清空同「owner+类型」的旧默认，保证 is_default 唯一。
    3. list_for_dispatch 按 default→weight 排序，供 M3/M7/M8 降级链取用。

实现逻辑：
    基于 async session 的 select/update/delete；用量聚合按 config/model/task_type 分组求和。
"""
import time

from sqlalchemy import select, func, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from models.llm_config import LLMConfig, LLMUsage


async def create(session: AsyncSession, cfg: LLMConfig) -> LLMConfig:
    """写入模型配置（调用方提交）。"""
    session.add(cfg)
    await session.flush()
    return cfg


async def get_visible(session: AsyncSession, owner_id: int, cfg_id: int) -> LLMConfig | None:
    """读取可见配置：本人或全局。"""
    cfg = await session.get(LLMConfig, cfg_id)
    if cfg and (cfg.owner_id == owner_id or cfg.owner_id is None):
        return cfg
    return None


async def get_owned(session: AsyncSession, owner_id: int, cfg_id: int) -> LLMConfig | None:
    """读取本人配置（改删/设默认前置校验）。"""
    cfg = await session.get(LLMConfig, cfg_id)
    if cfg and cfg.owner_id == owner_id:
        return cfg
    return None


async def list_configs(session: AsyncSession, owner_id: int, llm_type: str | None) -> list[LLMConfig]:
    """配置列表：本人 + 全局，可按 llm_type 过滤，按 weight/id 倒序。"""
    stmt = select(LLMConfig).where(
        or_(LLMConfig.owner_id == owner_id, LLMConfig.owner_id.is_(None))
    )
    if llm_type:
        stmt = stmt.where(LLMConfig.llm_type == llm_type)
    stmt = stmt.order_by(LLMConfig.weight.desc(), LLMConfig.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def list_for_dispatch(session: AsyncSession, owner_id: int, llm_type: str, lite: bool = False) -> list[LLMConfig]:
    """降级分发链（M9.8）：同类型 active 配置，默认优先、weight 次之。

    lite=True（M5 延迟敏感分支）：仅取已探针且开启 enable_lite 的配置，
    按实测 avg_latency_ms 升序（快者优先），供人物/章节分析等重延迟任务的「模型选型」。
    """
    if lite:
        stmt = select(LLMConfig).where(
            or_(LLMConfig.owner_id == owner_id, LLMConfig.owner_id.is_(None)),
            LLMConfig.llm_type == llm_type,
            LLMConfig.status == "active",
            LLMConfig.enable_lite.is_(True),
            LLMConfig.avg_latency_ms.isnot(None),
        ).order_by(LLMConfig.avg_latency_ms.asc(), LLMConfig.id.desc())
        return list((await session.execute(stmt)).scalars().all())
    stmt = select(LLMConfig).where(
        or_(LLMConfig.owner_id == owner_id, LLMConfig.owner_id.is_(None)),
        LLMConfig.llm_type == llm_type,
        LLMConfig.status == "active",
    ).order_by(LLMConfig.is_default.desc(), LLMConfig.weight.desc(), LLMConfig.id.desc())
    return list((await session.execute(stmt)).scalars().all())


async def record_probe_result(session: AsyncSession, cfg_id: int, avg_latency_ms: int, success_rate: float) -> None:
    """写回探针评测结果（M5 模型选型评测），更新延迟/成功率与时间戳。"""
    await session.execute(
        update(LLMConfig).where(LLMConfig.id == cfg_id).values(
            avg_latency_ms=avg_latency_ms,
            success_rate=success_rate,
            last_probe_at=func.now(),
        )
    )


async def probe_config(adapter, cfg_id: int, n: int = 3):
    """对单个配置跑轻量探针，返回 (avg_latency_ms, success_rate)（M5 模型选型评测）。

    需可用 LLM 端点（在线）：发 n 次一句话补全，统计平均延迟与成功率；
    结果由调用方经 record_probe_result 写回 story_llm_config（本函数不触碰 DB）。
    """
    sample = [{"role": "user", "content": "用一句话介绍你自己。"}]
    latencies: list[float] = []
    ok = 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            await adapter.chat(sample)
            ok += 1
            latencies.append((time.perf_counter() - t0) * 1000)
        except Exception:
            pass
    if not latencies:
        return None, 0.0
    return int(sum(latencies) / len(latencies)), ok / n


async def clear_default(session: AsyncSession, owner_id: int, llm_type: str) -> None:
    """清空同「owner+类型」旧默认，保证唯一。"""
    await session.execute(
        update(LLMConfig).where(
            LLMConfig.owner_id == owner_id,
            LLMConfig.llm_type == llm_type,
            LLMConfig.is_default.is_(True),
        ).values(is_default=False)
    )


async def delete_config(session: AsyncSession, cfg: LLMConfig) -> None:
    """删除模型配置（物理删除）。"""
    await session.delete(cfg)


async def usage_stats(session: AsyncSession, owner_id: int):
    """用量聚合（M9.5）：按 config/model/task_type 汇总调用数与 token/费用。"""
    stmt = select(
        LLMUsage.config_id,
        LLMUsage.model,
        LLMUsage.task_type,
        func.count().label("calls"),
        func.coalesce(func.sum(LLMUsage.tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(LLMUsage.tokens_out), 0).label("tokens_out"),
        func.coalesce(func.sum(LLMUsage.cost), 0).label("cost"),
    ).where(LLMUsage.owner_id == owner_id).group_by(
        LLMUsage.config_id, LLMUsage.model, LLMUsage.task_type
    )
    return (await session.execute(stmt)).all()
