-- ============================================================
-- V4_pgvector向量存储（M3 切割+向量化）
-- 依赖 V1（story_chunk）
-- 启用 pgvector 扩展，为 story_chunk 增加 embedding 向量列与余弦索引。
-- 字符集/时区规范同 V1。
-- ============================================================

-- 启用向量扩展（需数据库超级权限，仅首次执行；已存在则跳过）
CREATE EXTENSION IF NOT EXISTS vector;

-- 为切片表增加向量列：维度由嵌入模型决定，列级不固定维度（pgvector 支持变长 vector）
ALTER TABLE story_chunk ADD COLUMN IF NOT EXISTS embedding vector;

-- HNSW 近似索引（余弦距离）。若因历史数据维度不一致导致创建失败，可先 DROP 再重建：
--   DROP INDEX IF EXISTS idx_chunk_embedding;
--   CREATE INDEX idx_chunk_embedding ON story_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunk_embedding
    ON story_chunk USING hnsw (embedding vector_cosine_ops);
