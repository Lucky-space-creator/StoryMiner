-- V6_M13扩展功能（笔记/标签/收藏/审计日志/阅读进度 + 全局搜索）
-- 全局搜索复用既有 story_novel/story_chapter/story_chunk/story_character 表，无需新表。
-- 命名规范：story_ 前缀；owner_id 隔离；审计日志只增不改（无更新/删除）。

-- 笔记（M13.2）：可挂载到小说/章节/人物等目标
CREATE TABLE story_note (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    title        VARCHAR(255) NOT NULL DEFAULT '',
    content      TEXT         NOT NULL DEFAULT '',
    target_type  VARCHAR(32)  NOT NULL DEFAULT '',       -- novel/chapter/character/...
    target_id    BIGINT,                                 -- 关联目标 id（可空=游离笔记）
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_note_owner ON story_note(owner_id);

-- 标签（M13.4）：按用户维护，同名唯一
CREATE TABLE story_tag (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    name         VARCHAR(64) NOT NULL,
    color        VARCHAR(16) NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)                              -- 同用户标签名唯一
);
CREATE INDEX idx_tag_owner ON story_tag(owner_id);

-- 标签关联（M13.4）：标签 ↔ 目标对象的多对多桥表
CREATE TABLE story_tag_link (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    tag_id       BIGINT NOT NULL REFERENCES story_tag(id),
    target_type  VARCHAR(32) NOT NULL DEFAULT '',
    target_id    BIGINT      NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_tag_link_owner ON story_tag_link(owner_id);
CREATE INDEX idx_tag_link_tag   ON story_tag_link(tag_id);

-- 收藏（M13.6）：用户对任意目标的快捷收藏
CREATE TABLE story_favorite (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    title        VARCHAR(255) NOT NULL DEFAULT '',
    target_type  VARCHAR(32)  NOT NULL DEFAULT '',
    target_id    BIGINT       NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_favorite_owner ON story_favorite(owner_id);

-- 审计日志（M13.7）：关键操作留痕，只增不改
CREATE TABLE story_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    action       VARCHAR(64)  NOT NULL,                  -- create_note/delete_favorite/...
    target       VARCHAR(255) NOT NULL DEFAULT '',       -- 操作对象描述
    detail       JSONB        NOT NULL DEFAULT '{}',     -- 操作快照
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_owner ON story_audit_log(owner_id);

-- 阅读进度（M13.3）：按 (owner, novel) 记录最近阅读位置
CREATE TABLE story_reading_progress (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    novel_id     BIGINT NOT NULL REFERENCES story_novel(id),
    chapter_id   BIGINT,                                 -- 最近阅读章节（可空）
    position     INT         NOT NULL DEFAULT 0,         -- 章节内偏移
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, novel_id)                          -- 每用户每小说仅一条进度
);
CREATE INDEX idx_reading_owner ON story_reading_progress(owner_id);
