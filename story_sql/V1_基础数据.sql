-- ============================================================
-- V1_基础数据（核心业务）
-- 小说 / 章节 / 知识库 / 文档 / 切片 / 实体 / 关系 / 对话 / 模型
-- 执行顺序：先建本文件，再建 V2 / V3
--
-- 【字符集】UTF-8（PostgreSQL 在数据库级别设定，所有表/列继承 UTF-8）
--   建库语句（仅需执行一次）：
--   CREATE DATABASE story_rag WITH ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
--
-- 【时间字段统一规范】
--   1) 所有时间字段使用 TIMESTAMPTZ（带时区，建议统一存 UTC）；
--   2) created_at / updated_at 默认 now()；updated_at 由本文件末尾统一触发器维护；
--   3) 可空时间字段（deleted_at / started_at / finished_at）为 NULL 表示未删/未开始/未完成。
--
-- 【扩展字段约定】每个主表均含 extra JSONB NOT NULL DEFAULT '{}'，用于后续功能扩展，无需改表结构。
-- ============================================================

-- 用户（仅标识创建者 / 数据隔离，无角色权限分级）
CREATE TABLE IF NOT EXISTS story_user (
    id          BIGSERIAL PRIMARY KEY,
    username    VARCHAR(64)  NOT NULL UNIQUE,
    password    VARCHAR(255) NOT NULL,
    name        VARCHAR(64),
    avatar      VARCHAR(512),
    settings    JSONB        NOT NULL DEFAULT '{}',   -- 个性化设置（主题/语言等 M13.8）
    extra       JSONB        NOT NULL DEFAULT '{}',   -- 扩展预留
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 小说（顶层隔离单元）
CREATE TABLE IF NOT EXISTS story_novel (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(255) NOT NULL,
    author      VARCHAR(128),
    summary     TEXT,
    description TEXT,                                       -- 扩展：详细描述
    cover       VARCHAR(512),
    status      VARCHAR(16)  NOT NULL DEFAULT 'serial',     -- serial=连载 finished=完结
    tags        JSONB        NOT NULL DEFAULT '[]',
    file_hash   CHAR(64),
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_novel_owner ON story_novel(owner_id) WHERE deleted_at IS NULL;

-- 章节
CREATE TABLE IF NOT EXISTS story_chapter (
    id          BIGSERIAL PRIMARY KEY,
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    title       VARCHAR(255),
    volume      VARCHAR(64),                                -- 扩展：卷/部归类
    chapter_no  INT          NOT NULL DEFAULT 0,
    content     TEXT         NOT NULL,
    word_count  INT          NOT NULL DEFAULT 0,
    char_start  INT,
    char_end    INT,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_chapter_novel ON story_chapter(novel_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_chapter_owner ON story_chapter(owner_id);

-- 知识库
CREATE TABLE IF NOT EXISTS story_knowledge_base (
    id          BIGSERIAL PRIMARY KEY,
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    scope       VARCHAR(16) NOT NULL DEFAULT 'private',     -- private/team/public(仅展示)
    config      JSONB        NOT NULL DEFAULT '{}',
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,
    UNIQUE (novel_id, name)
);

-- 文档
CREATE TABLE IF NOT EXISTS story_document (
    id          BIGSERIAL PRIMARY KEY,
    kb_id       BIGINT NOT NULL REFERENCES story_knowledge_base(id),
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(255) NOT NULL,
    doc_type    VARCHAR(16) NOT NULL,                       -- txt/epub/pdf/docx
    object_key  VARCHAR(512),                               -- MinIO 原文件 key
    file_hash   CHAR(64),
    status      VARCHAR(16) NOT NULL DEFAULT 'pending',     -- pending/parsing/done/failed
    error       TEXT,
    word_count  INT DEFAULT 0,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_doc_kb ON story_document(kb_id) WHERE deleted_at IS NULL;

-- 切片
CREATE TABLE IF NOT EXISTS story_chunk (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT NOT NULL REFERENCES story_document(id),
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    kb_id       BIGINT NOT NULL REFERENCES story_knowledge_base(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    chapter_id  BIGINT REFERENCES story_chapter(id),
    idx         INT          NOT NULL,
    content     TEXT         NOT NULL,
    word_count  INT          DEFAULT 0,
    vector_id   VARCHAR(128),
    meta        JSONB        NOT NULL DEFAULT '{}',         -- 页码/字符区间/来源标题
    disabled    BOOLEAN      NOT NULL DEFAULT false,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chunk_kb ON story_chunk(kb_id);
CREATE INDEX IF NOT EXISTS idx_chunk_chapter ON story_chunk(chapter_id);
CREATE INDEX IF NOT EXISTS idx_chunk_owner ON story_chunk(owner_id);

-- 关系类型字典
CREATE TABLE IF NOT EXISTS story_relation_type (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT REFERENCES story_user(id),           -- NULL 表示系统内置
    code        VARCHAR(32) NOT NULL UNIQUE,
    label       VARCHAR(64) NOT NULL,
    color       VARCHAR(16) DEFAULT '#888888',
    description TEXT,
    builtin     BOOLEAN NOT NULL DEFAULT false,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 实体 / 人物
CREATE TABLE IF NOT EXISTS story_entity (
    id          BIGSERIAL PRIMARY KEY,
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(128) NOT NULL,
    type        VARCHAR(16) NOT NULL DEFAULT 'character',   -- character/place/org
    profile     JSONB        NOT NULL DEFAULT '{}',         -- 性别/身份/性格/外貌/口头禅
    description TEXT,
    avatar      VARCHAR(512),                               -- MinIO object_key
    prompt_tpl_id BIGINT,                                   -- 绑定 persona 模板(V2)
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,
    UNIQUE (novel_id, name, type)
);
CREATE INDEX IF NOT EXISTS idx_entity_novel ON story_entity(novel_id) WHERE deleted_at IS NULL;

-- 关系
CREATE TABLE IF NOT EXISTS story_relation (
    id          BIGSERIAL PRIMARY KEY,
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    source_id   BIGINT NOT NULL REFERENCES story_entity(id),
    target_id   BIGINT NOT NULL REFERENCES story_entity(id),
    type        VARCHAR(32) NOT NULL,
    evidence    TEXT,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, type)
);
CREATE INDEX IF NOT EXISTS idx_relation_novel ON story_relation(novel_id);

-- 大模型配置（提前定义，供 story_conversation.llm_config_id 外键引用）
CREATE TABLE IF NOT EXISTS story_llm_config (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT REFERENCES story_user(id),           -- NULL 表示全局
    name        VARCHAR(128) NOT NULL,
    provider    VARCHAR(32) NOT NULL,                       -- OpenAI/Claude/Ollama/智谱/通义
    model       VARCHAR(128) NOT NULL,
    base_url    VARCHAR(512),
    api_key     TEXT,                                       -- Fernet 加密存储
    llm_type    VARCHAR(16) NOT NULL DEFAULT 'chat',         -- chat/embed/image
    is_default  BOOLEAN NOT NULL DEFAULT false,
    weight      INT          NOT NULL DEFAULT 0,             -- 降级优先级(M9.8)，越大越优先
    timeout     INT          NOT NULL DEFAULT 60,            -- 调用超时(秒)
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 对话会话
CREATE TABLE IF NOT EXISTS story_conversation (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    title       VARCHAR(255),
    character_ids JSONB NOT NULL DEFAULT '[]',              -- 自选人物(M7.1)
    llm_config_id BIGINT REFERENCES story_llm_config(id),   -- 使用的模型配置
    system_prompt TEXT,                                     -- 对话时 system prompt 快照
    favorite    BOOLEAN NOT NULL DEFAULT false,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 对话消息（多轮）
CREATE TABLE IF NOT EXISTS story_conversation_message (
    id          BIGSERIAL PRIMARY KEY,
    conv_id     BIGINT NOT NULL REFERENCES story_conversation(id),
    role        VARCHAR(16) NOT NULL,                       -- user/assistant/system
    content     TEXT         NOT NULL,
    character_id BIGINT,                                    -- 群聊时标记发言人物
    tokens_in   INT,
    tokens_out  INT,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON story_conversation_message(conv_id, created_at);

-- ============================================================
-- 统一 updated_at 触发器（所有含 updated_at 的表共用）
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_story_user_updated_at      BEFORE UPDATE ON story_user      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_novel_updated_at     BEFORE UPDATE ON story_novel     FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_chapter_updated_at   BEFORE UPDATE ON story_chapter   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_kb_updated_at        BEFORE UPDATE ON story_knowledge_base FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_document_updated_at  BEFORE UPDATE ON story_document  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_chunk_updated_at     BEFORE UPDATE ON story_chunk      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_entity_updated_at    BEFORE UPDATE ON story_entity     FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_conversation_updated_at BEFORE UPDATE ON story_conversation FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_llm_config_updated_at BEFORE UPDATE ON story_llm_config FOR EACH ROW EXECUTE FUNCTION set_updated_at();
