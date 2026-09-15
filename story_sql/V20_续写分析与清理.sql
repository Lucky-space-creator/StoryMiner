-- ============================================================
-- V20_续写分析与清理（M8 缓存复用 + M12 模板残留清理）
-- 依赖 V1（story_novel / story_user / story_entity）
-- 字符集 UTF-8、时区规范同 V1。
-- ============================================================

-- 续写概览分析缓存表（M8 缓存复用）
-- 功能：概览/时间线/角色弧线三类分析结果按 (owner_id, novel_id, kind, detail) 各存一份最新，
--       覆盖式更新（幂等），前端打开即读缓存，避免重复调用 LLM 浪费资源。
-- 命名规范：story_writing_analysis（与项目 story_xx 前缀一致）。
CREATE TABLE IF NOT EXISTS story_writing_analysis (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT       NOT NULL,
    novel_id    BIGINT       NOT NULL,
    kind        VARCHAR(20)  NOT NULL,   -- summary / timeline / character_arc
    detail      VARCHAR(10)  NOT NULL DEFAULT 'brief',  -- brief / detail
    content     TEXT         NOT NULL DEFAULT '',
    word_count  INTEGER      NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_writing_analysis_key UNIQUE (owner_id, novel_id, kind, detail)
);

CREATE INDEX IF NOT EXISTS ix_writing_analysis_novel ON story_writing_analysis (novel_id, owner_id);

-- 清理 Prompt 模板（M12）残留
-- 删除已废弃的 story_prompt_template 表及其触发器
-- 移除 story_entity 上闲置的 prompt_tpl_id 列（M12 遗留，无任何读写）
-- 注：story_prompt_template 旧定义原在 V2_AI扩展能力.sql，现已并入 V2_AI扩展能力与续写.sql 同一版本线。
DROP TRIGGER IF EXISTS trg_story_prompt_tpl_updated_at ON story_prompt_template;
DROP TABLE IF EXISTS story_prompt_template;

ALTER TABLE story_entity DROP COLUMN IF EXISTS prompt_tpl_id;
