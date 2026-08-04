-- V18：为 story_llm_config 补充采样参数列
-- 背景：LangChain 适配器（core/llm/langchain_factory.py）读取 cfg.temperature / cfg.max_tokens，
--       但 V1 建表语句未包含这两列，导致运行期 AttributeError，阅读助手等全部对话功能 500。
-- 说明：生产环境由 core/main.py 的 lifespan 在启动时以幂等 ALTER 自动补齐；
--       本文件仅作版本记录与手动回放用途。

ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS temperature double precision NOT NULL DEFAULT 0.7;
ALTER TABLE story_llm_config ADD COLUMN IF NOT EXISTS max_tokens integer;
