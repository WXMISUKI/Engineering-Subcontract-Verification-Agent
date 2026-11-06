const timeFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

// 目前独立版数据库会话使用 UTC，无偏移的历史事件时间按 UTC 解读。
// 有明确偏移的时间保留原始时区信息，避免重复加八小时。
export function formatEventTime(value, { date = false } = {}) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(value)) return '—'
  const source = value.trim().replace(' ', 'T')
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(source) ? source : `${source}Z`
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) return '—'
  const parts = (date ? dateFormatter : timeFormatter).formatToParts(parsed)
  const get = type => parts.find(item => item.type === type)?.value
  return date ? `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}` : `${get('hour')}:${get('minute')}:${get('second')}`
}
