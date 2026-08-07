-- ============================================================
-- V21_章节漫剧列（M15）
-- 记录用户为某小说选定的连续章节范围（≤5 章）及出场角色，供下游 AI 漫剧生产。
-- 字符集 UTF-8、时间字段 TIMESTAMPTZ 规范同 V1（见 V1 文件头说明）
-- ============================================================

CREATE TABLE IF NOT EXISTS story_chapter_drama (
    id              BIGSERIAL PRIMARY KEY,
    novel_id       BIGINT       NOT NULL REFERENCES story_novel(id),
    owner_id       BIGINT       NOT NULL REFERENCES story_user(id),
    title          VARCHAR(255),
    chapter_from   INT          NOT NULL,                       -- 起始章节序号
    chapter_to     INT          NOT NULL,                       -- 结束章节序号（>= chapter_from，跨度<=5）
    character_ids  JSONB        NOT NULL DEFAULT '[]',          -- 出场角色 story_character.id 列表
    summary        VARCHAR(1024),
    extra          JSONB        NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at     TIMESTAMPTZ,
    -- 章节范围约束：结束 >= 起始，且跨度不超过 5 章
    CONSTRAINT chk_drama_span CHECK (chapter_to >= chapter_from AND (chapter_to - chapter_from + 1) <= 5)
);
CREATE INDEX IF NOT EXISTS idx_drama_novel ON story_chapter_drama(novel_id) WHERE deleted_at IS NULL;
