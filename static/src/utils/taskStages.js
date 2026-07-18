// 异步任务阶段/类型/状态的中文映射，前后端共用语义

export const STAGE_TEXT = {
  pending: '排队中',
  preparing: '准备中',
  parsing: '解析中',
  splitting: '切章中',
  chunking: '切分文档',
  embedding: '向量化中',
  storing: '写入索引',
  extracting: '抽取实体中',
  generating: '生成小传中',
  done: '已完成',
  failed: '失败',
}

export function stageText(s) {
  return STAGE_TEXT[s] || s || '处理中'
}

export const TYPE_TEXT = {
  parse: '解析',
  chunk: '切割/向量化',
  graph: '知识图谱',
  character: '人物小传',
}

export function typeText(t) {
  return TYPE_TEXT[t] || t || '任务'
}

export const STATUS_TEXT = { running: '进行中', success: '成功', failed: '失败' }

export function statusText(s) {
  return STATUS_TEXT[s] || s || '未知'
}
