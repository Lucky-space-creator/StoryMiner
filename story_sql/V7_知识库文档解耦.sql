-- ============================================================
-- V7_知识库文档解耦（方案A）
-- 背景：原文档强绑默认知识库（document.kb_id 非空），导致新建知识库文档列表恒为空。
-- 改造：文档仅归属小说（kb_id 可空），知识库通过链接表 story_kb_document 关联文档；
--       构建索引时单选文档纳入知识库并切分向量化。
-- 执行顺序：在 V1~V6 之后执行；create_all 已建表时本文件幂等可重复执行。
-- ============================================================

-- 1) 文档与知识库解耦：kb_id 改为可空
ALTER TABLE story_document ALTER COLUMN kb_id DROP NOT NULL;

-- 2) 新增知识库-文档关联表（复合主键，一份文档可纳入多个知识库）
CREATE TABLE IF NOT EXISTS story_kb_document (
    kb_id      BIGINT NOT NULL REFERENCES story_knowledge_base(id),
    doc_id     BIGINT NOT NULL REFERENCES story_document(id),
    owner_id   BIGINT NOT NULL REFERENCES story_user(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kb_id, doc_id)
);

-- 3) 迁移历史数据：原 kb_id 非空的文档，建立关联（幂等，重复执行忽略）
INSERT INTO story_kb_document (kb_id, doc_id, owner_id, created_at)
SELECT kb_id, id, owner_id, created_at
FROM story_document
WHERE kb_id IS NOT NULL AND deleted_at IS NULL
ON CONFLICT (kb_id, doc_id) DO NOTHING;
