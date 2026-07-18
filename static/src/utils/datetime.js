// 时间格式化工具：将后端 ISO 时间串统一显示为「年-月-日 时:分:秒」。

/**
 * 将 ISO 时间串格式化为 YYYY-MM-DD HH:mm:ss。
 * 入参为空或非合法时间时返回占位符 '-'。
 * @param {string|null|undefined} iso 后端返回的 ISO 时间串
 * @returns {string}
 */
export function formatDateTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
