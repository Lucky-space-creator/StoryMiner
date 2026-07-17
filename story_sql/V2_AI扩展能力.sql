-- ============================================================
-- V2_AI扩展能力（Skill / MCP / Prompt）
-- 依赖 V1（story_user / story_llm_config 等）
-- 字符集 UTF-8、时间字段 TIMESTAMPTZ 规范同 V1（见 V1 文件头说明）
-- 时间触发器函数 set_updated_at() 已在 V1 中创建，本文件仅挂载触发器。
-- ============================================================

-- 大模型技能
CREATE TABLE IF NOT EXISTS story_skill (
    id            BIGSERIAL PRIMARY KEY,
    owner_id      BIGINT NOT NULL REFERENCES story_user(id),
    name          VARCHAR(128) NOT NULL,
    description   TEXT,
    prompt_template TEXT     NOT NULL,
    trigger       VARCHAR(128),
    mount_point   VARCHAR(32) NOT NULL DEFAULT 'global',    -- dialogue/continue_write/extract/global
    variables     JSONB NOT NULL DEFAULT '[]',
    priority      INT    NOT NULL DEFAULT 0,                -- 挂载顺序(M10.4)，越大越先拼入
    enabled       BOOLEAN NOT NULL DEFAULT true,
    builtin       BOOLEAN NOT NULL DEFAULT false,
    extra         JSONB  NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- MCP 配置
CREATE TABLE IF NOT EXISTS story_mcp_config (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    transport   VARCHAR(16) NOT NULL,                       -- stdio/sse
    server_url  VARCHAR(512),
    command     VARCHAR(512),
    auth_type   VARCHAR(16) DEFAULT 'none',                 -- none/token/basic
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

-- 提示词模板
CREATE TABLE IF NOT EXISTS story_prompt_template (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    type        VARCHAR(32) NOT NULL,                       -- persona/extract/continue_write/summary/custom
    content     TEXT         NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    is_default  BOOLEAN NOT NULL DEFAULT false,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tpl_owner_type ON story_prompt_template(owner_id, type);

-- 挂载 updated_at 统一触发器
CREATE TRIGGER trg_story_skill_updated_at      BEFORE UPDATE ON story_skill        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_mcp_config_updated_at BEFORE UPDATE ON story_mcp_config   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_prompt_tpl_updated_at BEFORE UPDATE ON story_prompt_template FOR EACH ROW EXECUTE FUNCTION set_updated_at();
