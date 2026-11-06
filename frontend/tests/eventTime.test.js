import { describe, expect, it } from 'vitest'
import { formatEventTime } from '../src/plus-ui/blockchain-supervision/lib/eventTime'

describe('事件时间使用北京时间', () => {
  it('将数据库无时区的 UTC 时间转换为北京时间', () => {
    expect(formatEventTime('2026-11-06T01:00:30')).toBe('09:00:30')
    expect(formatEventTime('2026-11-05T18:00:00', { date: true })).toBe('11-06 02:00')
  })
  it('尊重 UTC 和明确时区偏移，避免重复加八小时', () => {
    expect(formatEventTime('2026-11-06T01:00:30Z')).toBe('09:00:30')
    expect(formatEventTime('2026-11-06T09:00:30+08:00')).toBe('09:00:30')
    expect(formatEventTime('2026-11-06T02:00:30+01:00')).toBe('09:00:30')
  })
  it('缺失或无效时间显示占位符', () => {
    expect(formatEventTime(null)).toBe('—')
    expect(formatEventTime('not-a-date')).toBe('—')
  })
})
