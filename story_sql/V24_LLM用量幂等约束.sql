-- V24：story_llm_usage 增加 task_id 幂等约束
-- 背景：P2-17 缺陷——record_llm_usage 缺少 task_id 唯一约束，重试/补偿写入时可能产生重复计费记录，
--       导致仪表盘「Token 用量趋势 / 模型占比」系统性高估。
-- 修复：新增 task_id 列并建立唯一索引，配合应用层 INSERT ... ON CONFLICT DO NOTHING 实现幂等写入。
-- 说明：task_id 允许为 NULL（非任务触发的直接调用），PostgreSQL 中 NULL 不参与唯一约束，可存在多行。

ALTER TABLE story_llm_usage ADD COLUMN task_id BIGINT;

-- 唯一索引：同一任务的用量记录仅写入一次；重复写入由应用层 ON CONFLICT DO NOTHING 静默忽略
CREATE UNIQUE INDEX uq_llm_usage_task ON story_llm_usage (task_id);
