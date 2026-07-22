-- 混合分析管道 M5：模型选型评测字段（结果缓存为进程内 TTLCache，无需 DB 表）
-- 仅对 story_llm_config 增加选型评测列，幂等可重复执行（IF NOT EXISTS）。

ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS enable_lite BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS avg_latency_ms INTEGER;
ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS success_rate NUMERIC(5, 4);
ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS last_probe_at TIMESTAMPTZ;
