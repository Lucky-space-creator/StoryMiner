-- ============================================================
-- V5_人物档案与切片向量（M6 人物详情 + M3 切片向量列兼容 Chroma）
-- 依赖 V1（story_novel / story_user / story_chunk）
-- 字符集 UTF-8、时间字段 TIMESTAMPTZ 规范同 V1。
-- ============================================================

-- M6 小说人物信息简介
-- 与 M5 的 story_entity（图谱节点）解耦：本表承载人物详情/小传/画像，支持手动维护与 LLM 生成。
-- 命名规范：story_ 前缀；owner_id 隔离；软删除 deleted_at。
CREATE TABLE IF NOT EXISTS story_character (
    id           BIGSERIAL PRIMARY KEY,
    novel_id     BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id     BIGINT NOT NULL REFERENCES story_user(id),
    name         VARCHAR(128) NOT NULL,
    role         VARCHAR(32)  NOT NULL DEFAULT '配角',   -- 主角/配角/反派/势力
    gender       VARCHAR(16),                            -- 男/女/未知
    identity     VARCHAR(255),                           -- 身份/职业
    personality  VARCHAR(512),                          -- 性格
    appearance   VARCHAR(512),                          -- 外貌
    catchphrase  VARCHAR(255),                          -- 口头禅
    description  TEXT,                                   -- 简介/小传（AI 生成或手填）
    avatar       VARCHAR(512),                          -- 头像 object_key（M6.6 预留）
    source       VARCHAR(16)  NOT NULL DEFAULT 'manual',-- manual/auto
    appearances  INT          NOT NULL DEFAULT 0,        -- 出场次数（生成时统计）
    extra        JSONB        NOT NULL DEFAULT '{}',     -- 扩展画像（标签云等）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    UNIQUE (novel_id, name)                             -- 同小说人物名唯一
);

CREATE INDEX IF NOT EXISTS idx_character_novel ON story_character(novel_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_character_owner ON story_character(owner_id);

-- M3 切割+向量化 补丁：当前向量库为 Chroma（config.yml vector_store.type=chroma），
-- 向量实际存于 Chroma；story_chunk.embedding 仅作冗余保留字段。
-- ORM 模型 Chunk.embedding 使用 PgVectorStr(impl=Text) 以字符串读写，
-- 故此处以 TEXT 类型补齐列，避免依赖 pgvector 扩展与 asyncpg 自定义类型注册。
ALTER TABLE story_chunk ADD COLUMN IF NOT EXISTS embedding TEXT;
