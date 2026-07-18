-- V8 统一异步任务表（story_async_task）
-- 背景：原先解析/切割/图谱抽取/人物生成的进度分散在多处（解析入库、切割内存字典、图谱无记录），
--       前端无法统一展示。本版本收敛为一张统一任务表，供仪表盘总览与全局轮询读取。
-- 适用：所有向量模型/AI 耗时任务（解析 parse / 切割向量化 chunk / 知识图谱抽取 graph / 人物小传 character）。

CREATE TABLE IF NOT EXISTS story_async_task (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id    BIGINT NOT NULL,
    type        VARCHAR(32) NOT NULL,
    name        VARCHAR(255) NOT NULL,
    novel_id    BIGINT,
    kb_id       BIGINT,
    doc_id      BIGINT,
    target_id   BIGINT,
    stage       VARCHAR(32) NOT NULL DEFAULT 'pending',
    progress    INTEGER NOT NULL DEFAULT 0,
    status      VARCHAR(16) NOT NULL DEFAULT 'running',
    error       TEXT,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    extra       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_async_task_owner ON story_async_task (owner_id);
CREATE INDEX IF NOT EXISTS idx_async_task_owner_status ON story_async_task (owner_id, status);
