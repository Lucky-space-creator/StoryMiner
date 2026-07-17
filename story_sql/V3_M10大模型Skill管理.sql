-- ============================================================
-- V3_M10 大模型 Skill 管理
-- 功能：可复用提示词任务包（M10.1~M10.6）
-- 说明：版本化 DDL 存档；运行期由 main.lifespan 自动 create_all 建表，
--       并 seed 内置 Skill 库（builtin=TRUE，幂等）。
-- 依赖：story_llm_config（M9，调试实际调用时取 chat 模型）
-- ============================================================

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
