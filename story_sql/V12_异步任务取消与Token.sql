-- V12：统一异步任务支持「用户主动取消」与 Token 消耗回写
-- 执行顺序：在 V1~V11 之后执行；运行期由 main.lifespan 自动 ALTER（已兼容幂等）。
-- 说明：
--   1. story_async_task 增加 tokens_in / tokens_out 列，后台任务边跑边回写，
--      取消/失败时也能体现已消耗额度（取消时按已消耗 token 记录到 story_llm_usage）。
--   2. 状态机新增 cancelled：用户在前端取消 running 任务后，后台协程在循环边界感知并停止，
--      回写 status=cancelled 与错误原因（人物分析→「用户主动取消人物」等）。
--   3. 取消标志为进程内内存集合，取消端点登记标志并立即回写状态，提供秒级反馈。

ALTER TABLE story_async_task ADD COLUMN IF NOT EXISTS tokens_in INTEGER NOT NULL DEFAULT 0;
ALTER TABLE story_async_task ADD COLUMN IF NOT EXISTS tokens_out INTEGER NOT NULL DEFAULT 0;
