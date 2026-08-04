-- V19 长任务差异化展示与独立管理
-- 新增预估耗时、长任务标记、预计完成时刻字段
-- story_async_task 表用于统一管理所有异步任务的进度

ALTER TABLE story_async_task
    ADD COLUMN IF NOT EXISTS estimated_duration_minutes integer,
    ADD COLUMN IF NOT EXISTS is_long_task boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS estimated_complete_at timestamp with time zone;

-- 为长任务筛选查询加速
CREATE INDEX IF NOT EXISTS idx_async_task_long
    ON story_async_task (owner_id, is_long_task, status)
    WHERE is_long_task = true;
