-- V16 阅读辅助对话：在原有 story_conversation / story_conversation_message 表上扩展字段
-- 设计思路：复用对话表（用户隔离 owner_id + 按小说 novel_id），仅补充阅读辅助对话所需字段，避免重建表。
-- 关键点：上下文窗口限制（context_window）、压缩保留（compressed_summary）、附件（attachments）。

ALTER TABLE story_conversation
  ADD COLUMN IF NOT EXISTS context_window integer NOT NULL DEFAULT 4000,
  ADD COLUMN IF NOT EXISTS keep_recent integer NOT NULL DEFAULT 10,
  ADD COLUMN IF NOT EXISTS compressed_summary text;

ALTER TABLE story_conversation_message
  ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS is_compressed boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS tokens integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN story_conversation.context_window IS '上下文 token 预算，超出触发历史压缩';
COMMENT ON COLUMN story_conversation.keep_recent IS '压缩时始终保留的最近消息条数';
COMMENT ON COLUMN story_conversation.compressed_summary IS '历史对话压缩后的摘要，用于保留上下文';
COMMENT ON COLUMN story_conversation_message.attachments IS '附件列表：[{type:image|file,url,name,size}]';
COMMENT ON COLUMN story_conversation_message.is_compressed IS '该消息是否为压缩生成的内容';
COMMENT ON COLUMN story_conversation_message.tokens IS '该消息累计 token（in+out），用于上下文预算统计';
