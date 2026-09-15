-- ============================================================
-- V2_AI扩展能力与续写（MCP / Skill 支撑 + M8 情节概览与续写）
-- 依赖 V1（story_user / story_llm_config 等）
-- 字符集 UTF-8、时间字段 TIMESTAMPTZ 规范同 V1（见 V1 文件头说明）
-- 时间触发器函数 set_updated_at() 已在 V1 中创建，本文件仅挂载触发器。
-- 说明：大模型技能 story_skill 的权威建表定义见 V3_M10大模型Skill管理.sql，
--       本文件不再重复定义，避免与运行时 ORM（core/models/skill.py）结构冲突（owner_id 可空）。
-- ============================================================

-- MCP 配置
CREATE TABLE IF NOT EXISTS story_mcp_config (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    transport   VARCHAR(16) NOT NULL,                       -- stdio/sse
    server_url  VARCHAR(512),
    command     VARCHAR(512),
    auth_type   VARCHAR(16) DEFAULT 'none',                -- none/token/basic
    auth_meta   JSONB NOT NULL DEFAULT '{}',                -- token/用户名等(加密)
    timeout     INT   NOT NULL DEFAULT 30,                  -- 调用超时(秒)
    status      VARCHAR(16) NOT NULL DEFAULT 'inactive',    -- active/inactive/error
    tools_cache JSONB NOT NULL DEFAULT '[]',
    extra       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- MCP 调用日志
CREATE TABLE IF NOT EXISTS story_mcp_log (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    mcp_id      BIGINT NOT NULL REFERENCES story_mcp_config(id),
    tool        VARCHAR(128) NOT NULL,
    input       JSONB,
    output      TEXT,
    status      VARCHAR(16) NOT NULL,
    elapsed_ms  INT,
    extra       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mcplog_owner ON story_mcp_log(owner_id, created_at);

-- 挂载 updated_at 统一触发器
CREATE TRIGGER trg_story_mcp_config_updated_at BEFORE UPDATE ON story_mcp_config   FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- V2 M8 情节概览与续写
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
