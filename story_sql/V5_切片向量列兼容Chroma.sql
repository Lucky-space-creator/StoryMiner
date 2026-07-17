-- ============================================================
-- V5 切片向量列兼容 Chroma（M3 切割+向量化 补丁）
-- 依赖 V1（story_chunk）
-- 说明：当前向量库为 Chroma（config.yml vector_store.type=chroma），
--   向量实际存于 Chroma；story_chunk.embedding 仅作冗余保留字段。
--   ORM 模型 Chunk.embedding 使用 PgVectorStr(impl=Text) 以字符串读写，
--   故此处以 TEXT 类型补齐列，避免依赖 pgvector 扩展与 asyncpg 自定义类型注册。
-- 字符集/时区规范同 V1。
-- ============================================================

ALTER TABLE story_chunk ADD COLUMN IF NOT EXISTS embedding TEXT;
