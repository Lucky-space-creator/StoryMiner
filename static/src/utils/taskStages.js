// 异步任务阶段/类型/状态的中文映射，前后端共用语义

export const STAGE_TEXT = {
  pending: '排队中',
  preparing: '准备中',
  clearing: '清空旧数据中',
  parsing: '解析中',
  splitting: '切章中',
  chunking: '切分文档',
  embedding: '向量化中',
  storing: '写入索引',
  extracting: '抽取实体中',
  generating: '生成小传中',
  structure: '分析结构中',
  analyzing: '分析中',
  reading: '正在解析小说',
  summarizing: '正在生成摘要',
  char_extract: '正在归纳人物',
  char_experience: '正在总结人物经历',
  char_events: '正在提取关键事件',
  char_save: '正在保存到人物档案',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export function stageText(s) {
  // V13: 支持 extracting_N/M 格式 "正在抽取第N/M块"
  if (s && s.startsWith('extracting_')) {
    const parts = s.replace('extracting_', '').split('/')
    if (parts.length === 2) return `正在抽取第${parts[0]}/${parts[1]}块`
  }
  return STAGE_TEXT[s] || s || '处理中'
}

export const TYPE_TEXT = {
  parse: '解析',
  chunk: '切割/向量化',
  graph: '知识图谱',
  character: '人物小传',
  character_analysis: '人物分析',
  chapter_analysis: '章节解析',
}

export function typeText(t) {
  return TYPE_TEXT[t] || t || '任务'
}

export const STATUS_TEXT = { running: '进行中', success: '成功', failed: '失败', cancelled: '已取消' }

export function statusText(s) {
  return STATUS_TEXT[s] || s || '未知'
}
