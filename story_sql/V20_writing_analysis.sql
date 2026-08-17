-- V20 续写概览分析缓存表（M8 缓存复用）
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
