-- V23 章节漫剧场景增加「上集回顾」字段（M15.5 需求2）
-- 整体思路：单章漫剧片段需在前置「上一章大致内容与剧情发展」（100–200 字），
-- 由导演 Agent 基于上一章正文生成，存于 story_chapter_drama_scene.prev_chapter_review，
-- 供前端在漫剧片段顶部展示，帮助观众衔接上下文。
-- 关键点：
--   1. 仅新增一列 TEXT，可空（开篇无上一章时为 NULL）。
--   2. 幂等：IF NOT EXISTS 保护，重跑不报错。
ALTER TABLE story_chapter_drama_scene
    ADD COLUMN IF NOT EXISTS prev_chapter_review TEXT;
