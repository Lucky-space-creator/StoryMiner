-- ============================================================
-- V20_清理 Prompt 模板（M12）残留
-- 删除已废弃的 story_prompt_template 表及其触发器
-- 移除 story_entity 上闲置的 prompt_tpl_id 列（M12 遗留，无任何读写）
-- 依赖 V2_AI扩展能力.sql（story_prompt_template 原定义在此）
-- ============================================================

-- 1. 删除 story_prompt_template 触发器与表
DROP TRIGGER IF EXISTS trg_story_prompt_tpl_updated_at ON story_prompt_template;
DROP TABLE IF EXISTS story_prompt_template;

-- 2. 移除 story_entity.prompt_tpl_id 列（若存在）
ALTER TABLE story_entity DROP COLUMN IF EXISTS prompt_tpl_id;
