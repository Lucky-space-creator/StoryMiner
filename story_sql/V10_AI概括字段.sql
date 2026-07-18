-- V10 小说 AI 概括字段（story_novel.ai_summary）
-- 背景：需求要求在创建小说时若上传了小说文档，需要异步生成一份 200 字以内的
--       AI 概括（含主人公与大体情节），展示在用户简介下方作为「AI 概括」。
-- 实现：在 story_novel 表新增 ai_summary TEXT 字段（可空），由后台任务在解析完成后回写。
--       不直接覆盖用户填写的 summary，二者独立存在，前端分别展示。

ALTER TABLE story_novel
    ADD COLUMN IF NOT EXISTS ai_summary TEXT;

COMMENT ON COLUMN story_novel.ai_summary IS 'AI 生成的小说概括（≤200字，含主人公与大体情节），由异步任务在文档解析完成后回写';
