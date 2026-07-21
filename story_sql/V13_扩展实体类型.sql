-- ============================================================
-- V13_扩展实体类型（知识图谱增强）
-- 整体思路：
--   扩展 story_entity.type 字段长度，新增 story_entity_type 实体类型字典表，
--   将实体类型从3种（人物/地点/组织）扩展为7种（人物/地点/组织/时间/事件/物品/概念）。
--
-- 关键点：
--   1. VARCHAR(16) → VARCHAR(32) 是安全扩长，不影响已有数据。
--   2. 实体类型字典表支持系统内置 + 用户自定义扩展。
--   3. 种子数据覆盖7种基础类型，含图谱节点颜色与可视化符号。
--
-- 实现逻辑：
--   ALTER COLUMN 扩长 → CREATE TABLE 新建字典表 → INSERT 种子数据。
-- ============================================================

-- 1. 扩展实体类型字段长度
ALTER TABLE story_entity ALTER COLUMN type TYPE VARCHAR(32);

-- 2. 新建实体类型字典表
CREATE TABLE IF NOT EXISTS story_entity_type (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(32) NOT NULL UNIQUE,
    label       VARCHAR(64) NOT NULL,
    color       VARCHAR(16)  NOT NULL DEFAULT '#888888',
    symbol      VARCHAR(16)  NOT NULL DEFAULT 'circle',     -- ECharts 节点形状
    builtin     BOOLEAN      NOT NULL DEFAULT true,
    extra       JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 3. 预置7种实体类型种子数据
INSERT INTO story_entity_type (code, label, color, symbol, builtin) VALUES
    ('character',   '人物', '#0d9488', 'circle',      true),
    ('place',       '地点', '#6366f1', 'rect',        true),
    ('org',         '组织', '#d97706', 'diamond',     true),
    ('time_period', '时间', '#8b5cf6', 'triangle',    true),
    ('event',       '事件', '#dc2626', 'roundRect',   true),
    ('item',        '物品', '#16a34a', 'pin',         true),
    ('concept',     '概念', '#0891b2', 'arrow',       true)
ON CONFLICT (code) DO NOTHING;
