-- ============================================================
-- V11 移除切片冗余向量列（Chroma-only 改造）
-- 依赖 V1（story_chunk）
-- 说明：向量库已统一为 Chroma（config.yml vector_store.type=chroma），
--   story_chunk.embedding 为 V4/V5 在 PG 冗余存储的向量列（含 HNSW 索引），
--   Chroma 已承载全部向量读写，该列不再被 ORM 使用，予以清理。
--   本脚本幂等，可重复执行；字符集/时区规范同 V1。
-- ============================================================

-- 先删依赖该列的 HNSW 索引（不存在则跳过）
DROP INDEX IF EXISTS idx_chunk_embedding;

-- 删除冗余向量列（不存在则跳过）
ALTER TABLE story_chunk DROP COLUMN IF EXISTS embedding;
