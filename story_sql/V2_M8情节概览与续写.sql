-- ============================================================
-- V2_M8 情节概览与续写
-- 说明：M8 仅续写版本需要持久化（支撑多版本对比 M8.6 与入库采纳 M8.7）。
--       情节概览 / 时间线 / 角色弧线为实时 LLM 生成，不落库。
-- 注：后端 main.py 已通过 lifespan 在启动时自动 create_all 该表，
--     本文件作为版本化 DDL 存档，与 ORM 模型 core/models/story_writing.py 保持一致。
-- ============================================================

CREATE TABLE IF NOT EXISTS story_continue_write (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      BIGINT       NOT NULL,
    novel_id      BIGINT       NOT NULL,
    chapter_id    BIGINT       NULL,
    title         VARCHAR(255) NULL,
    style         VARCHAR(16)  NOT NULL DEFAULT 'original',
    length        VARCHAR(16)  NOT NULL DEFAULT 'mid',
    perspective   VARCHAR(16)  NOT NULL DEFAULT 'third',
    prompt        TEXT         NULL,
    content       TEXT         NOT NULL,
    word_count    INTEGER      NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ  NULL
);

CREATE INDEX IF NOT EXISTS idx_cw_owner ON story_continue_write (owner_id);
CREATE INDEX IF NOT EXISTS idx_cw_novel ON story_continue_write (novel_id);
