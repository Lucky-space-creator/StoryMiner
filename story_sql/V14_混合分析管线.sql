-- V14_混合分析管线
-- 混合分析管道（Hybrid Pipeline）确定性阶段产物落库
-- 用途：避免重复计算，并支撑图谱共现推理（P3）

-- 确定性阶段产出的人物/实体候选名单（含频次、首现章节）
CREATE TABLE story_ner_candidate (
    id              BIGSERIAL PRIMARY KEY,
    novel_id        BIGINT NOT NULL REFERENCES story_novel(id) ON DELETE CASCADE,
    name            VARCHAR(64) NOT NULL,
    entity_type     VARCHAR(32) NOT NULL DEFAULT 'character',
    freq            INT NOT NULL DEFAULT 0,
    first_chapter   INT,
    extra           JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_ner_candidate_novel ON story_ner_candidate(novel_id);

-- 共现矩阵（lighter 表，仅存权重大于阈值的边）
CREATE TABLE story_cooccurrence (
    id          BIGSERIAL PRIMARY KEY,
    novel_id    BIGINT NOT NULL REFERENCES story_novel(id) ON DELETE CASCADE,
    source      VARCHAR(64) NOT NULL,
    target      VARCHAR(64) NOT NULL,
    weight      INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_cooccurrence_novel ON story_cooccurrence(novel_id);
