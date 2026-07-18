-- V9 确保统一异步任务表已包含 name 列
-- 背景：上一轮要求异步任务名称使用「小说名-业务」格式（如 小说xx-构建索引）。
--       若线上库是用更早的表结构创建的，可能缺少 name 列，导致 create_task 写入的名字无法落库。
--       本迁移幂等地为 story_async_task 补上 name 列，保证任务名一定持久化。

ALTER TABLE story_async_task
    ADD COLUMN IF NOT EXISTS name VARCHAR(255) NOT NULL DEFAULT '未命名任务';

-- 对历史无名字记录回填一个可读默认名（按类型区分），便于仪表盘展示。
UPDATE story_async_task
SET name = CASE type
    WHEN 'parse'     THEN '文档解析任务'
    WHEN 'chunk'     THEN '构建索引任务'
    WHEN 'graph'     THEN '知识图谱抽取任务'
    WHEN 'character' THEN '人物抽取实体任务'
    ELSE '未命名任务'
END
WHERE name = '未命名任务';
