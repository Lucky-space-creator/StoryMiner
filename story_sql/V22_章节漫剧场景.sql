-- V22 章节漫剧场景分析表（M15.5 导演 Agent）
-- 整体思路：每个章节漫剧(drama_id)对应一条场景分析，由「导演 Agent」基于章节正文与出场角色生成，
-- 包含 场景设计 / 剧情安排 / 镜头运转 / 预计时长 四类列表，用户可保存在此表。
-- 关键点：
--   1. drama_id 唯一（一个漫剧一段场景分析），owner_id 做数据隔离。
--   2. 四个列表字段用 JSONB 数组存储，单条元素为字符串（或简单对象）。
--   3. content_raw 留存模型原始输出，便于排查。
CREATE TABLE IF NOT EXISTS story_chapter_drama_scene (
    id              BIGSERIAL PRIMARY KEY,
    drama_id        BIGINT      NOT NULL REFERENCES story_chapter_drama(id) ON DELETE CASCADE,
    owner_id        BIGINT      NOT NULL,
    novel_id        BIGINT      NOT NULL,
    scene_design    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    plot_arrangement JSONB      NOT NULL DEFAULT '[]'::jsonb,
    camera_movement JSONB       NOT NULL DEFAULT '[]'::jsonb,
    duration_estimate TEXT,
    content_raw     TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_drama_scene_unique ON story_chapter_drama_scene(drama_id);
CREATE INDEX IF NOT EXISTS idx_drama_scene_owner ON story_chapter_drama_scene(owner_id);
CREATE INDEX IF NOT EXISTS idx_drama_scene_novel ON story_chapter_drama_scene(novel_id);
