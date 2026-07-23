-- V17 极速模式分析摘要表
-- 功能：为「极速模式(turbo)」产出的人物画像/情节概览/关系概览摘要提供持久化，
--       与深度模式(逐实体落结构化库)并存、互不影响。按 (novel_id, analysis_type) 存一份最新摘要。
-- 说明：项目启动时 Base.metadata.create_all 已自动建表（幂等），本文件仅作版本留痕与手动补表使用。

CREATE TABLE IF NOT EXISTS story_analysis_summary (
    id              SERIAL PRIMARY KEY,
    novel_id        INTEGER NOT NULL,
    owner_id        INTEGER NOT NULL,
    analysis_type   VARCHAR(20) NOT NULL,   -- character / chapter / graph
    mode            VARCHAR(20) NOT NULL DEFAULT 'turbo',
    content         TEXT    NOT NULL DEFAULT '',
    fmt             VARCHAR(10) NOT NULL DEFAULT 'markdown',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_summary_novel_id ON story_analysis_summary (novel_id);
CREATE INDEX IF NOT EXISTS ix_summary_owner_id ON story_analysis_summary (owner_id);
CREATE INDEX IF NOT EXISTS ix_summary_novel_type ON story_analysis_summary (novel_id, analysis_type);
