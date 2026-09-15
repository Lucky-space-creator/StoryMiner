-- ============================================================
-- V3_大模型Skill管理与仪表盘增强（M10 Skill + 任务/计费/扩展）
-- 依赖 V1（story_user / story_novel / story_document / story_llm_config）
-- 字符集 UTF-8、时间字段 TIMESTAMPTZ 规范同 V1（见 V1 文件头说明）
-- 时间触发器函数 set_updated_at() 已在 V1 中创建。
-- 说明：原 V3_M10大模型Skill管理.sql 与 V3_仪表盘与增强.sql 同号冲突，
--       现合并为单一文件，消除重复版本号。story_skill 以本文件定义为权威
--       （owner_id 可空，与运行时 ORM core/models/skill.py 对齐）。
-- ============================================================

-- 大模型技能（M10.1~M10.6 可复用提示词任务包）
-- 版本化 DDL 存档；运行期由 main.lifespan 自动 create_all 建表，并 seed 内置 Skill 库（builtin=TRUE，幂等）。
CREATE TABLE IF NOT EXISTS story_skill (
    id              BIGSERIAL PRIMARY KEY,
    owner_id        BIGINT        NULL,          -- 用户隔离；NULL 表示全局内置 Skill
    name            VARCHAR(128)  NOT NULL,
    description     TEXT          NULL,
    prompt_template TEXT          NOT NULL,       -- Jinja2 模板，变量由 mount_point 约定
    trigger         VARCHAR(128)  NULL,          -- 触发条件描述
    mount_point     VARCHAR(32)   NOT NULL DEFAULT 'global',  -- dialogue/continue_write/extract/global
    enabled         BOOLEAN       NOT NULL DEFAULT TRUE,
    builtin         BOOLEAN       NOT NULL DEFAULT FALSE,       -- 内置库标记，仅可启停不可删
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_owner ON story_skill (owner_id);
CREATE INDEX IF NOT EXISTS idx_skill_mount ON story_skill (mount_point);

-- 解析异步任务（状态机：pending→parsing→splitting→chunking→done/failed）
CREATE TABLE IF NOT EXISTS story_parse_task (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    novel_id    BIGINT REFERENCES story_novel(id),
    doc_id      BIGINT REFERENCES story_document(id),
    stage       VARCHAR(32) NOT NULL DEFAULT 'pending',     -- pending/parsing/splitting/chunking/done/failed
    progress    INT          NOT NULL DEFAULT 0,            -- 0~100
    status      VARCHAR(16) NOT NULL DEFAULT 'running',     -- running/success/failed
    error       TEXT,
    celery_id   VARCHAR(64),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_owner ON story_parse_task(owner_id, status);

-- 模型调用计费（高频写入，建议批量/异步落库）
CREATE TABLE IF NOT EXISTS story_llm_usage (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    config_id   BIGINT REFERENCES story_llm_config(id),
    model       VARCHAR(128),                               -- 冗余模型名，便于统计无需连表
    task_type   VARCHAR(32) NOT NULL,                       -- dialogue/continue_write/extract/embed/summary
    tokens_in   INT          NOT NULL DEFAULT 0,
    tokens_out  INT          NOT NULL DEFAULT 0,
    cost        NUMERIC(12,6) NOT NULL DEFAULT 0,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_owner ON story_llm_usage(owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_config ON story_llm_usage(config_id);

-- 笔记 / 批注
CREATE TABLE IF NOT EXISTS story_note (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    target_type VARCHAR(32) NOT NULL,                       -- chapter/chunk/character
    target_id   BIGINT NOT NULL,
    content     TEXT NOT NULL,
    extra       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 阅读进度
CREATE TABLE IF NOT EXISTS story_reading_progress (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id),
    chapter_id  BIGINT REFERENCES story_chapter(id),
    position    INT NOT NULL DEFAULT 0,                      -- 字符偏移
    extra       JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, novel_id)
);

-- 标签
CREATE TABLE IF NOT EXISTS story_tag (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    name        VARCHAR(64) NOT NULL,
    color       VARCHAR(16) DEFAULT '#409EFF',              -- 标签颜色
    description TEXT,
    UNIQUE (owner_id, name)
);

-- 标签关联
CREATE TABLE IF NOT EXISTS story_tag_rel (
    tag_id      BIGINT NOT NULL REFERENCES story_tag(id),
    target_type VARCHAR(32) NOT NULL,                       -- novel/character/chapter
    target_id   BIGINT NOT NULL,
    PRIMARY KEY (tag_id, target_type, target_id)
);

-- 收藏
CREATE TABLE IF NOT EXISTS story_favorite (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    target_type VARCHAR(32) NOT NULL,                       -- character/chapter/conversation
    target_id   BIGINT NOT NULL,
    extra       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, target_type, target_id)
);

-- 审计日志
CREATE TABLE IF NOT EXISTS story_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES story_user(id),
    action      VARCHAR(32) NOT NULL,                       -- create/update/delete
    target      VARCHAR(64) NOT NULL,
    target_id   BIGINT,
    detail      JSONB,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_owner ON story_audit_log(owner_id, created_at);

-- 挂载 updated_at 统一触发器
CREATE TRIGGER trg_story_note_updated_at  BEFORE UPDATE ON story_note  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_progress_updated_at BEFORE UPDATE ON story_reading_progress FOR EACH ROW EXECUTE FUNCTION set_updated_at();
