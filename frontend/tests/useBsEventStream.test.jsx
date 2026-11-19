import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useBsEventStream } from '../src/plus-ui/blockchain-supervision/hooks/useBsEventStream'

const streams = []
class FakeEventSource {
  handlers = new Map()
  close = vi.fn()
  constructor(url, options) { this.url = url; this.options = options; streams.push(this) }
  addEventListener(type, fn) { this.handlers.set(type, fn) }
  emit(type, dto) { act(() => this.handlers.get(type)?.({ data: JSON.stringify(dto) })) }
}
afterEach(() => { cleanup(); streams.length = 0; vi.unstubAllGlobals() })

it('replays, deduplicates, isolates run identity and ignores late frames after switching tasks', () => {
  vi.stubGlobal('EventSource', FakeEventSource)
  const hook = renderHook(({ taskId, runId }) => useBsEventStream(taskId, runId, { retrospective: true }),
    { initialProps: { taskId: 4, runId: 'r1' } })
  const first = streams[0]
  expect(first.url).toBe('/api/plus/blockchain-supervision/tasks/4/events')
  expect(first.options.withCredentials).toBe(true)
  first.emit('retrospective.accepted', { sequence: 1, run_id: 'old' })
  first.emit('retrospective.batch_started', { sequence: 2, run_id: 'r1' })
  first.emit('retrospective.batch_started', { sequence: 2, run_id: 'r1' })
  first.emit('retrospective.batch_completed', { sequence: 3, run_id: 'r1' })
  expect(hook.result.current.map((row) => row.sequence)).toEqual([2, 3])
  hook.rerender({ taskId: 5, runId: 'r2' })
  expect(first.close).toHaveBeenCalled()
  first.emit('retrospective.completed', { sequence: 4, run_id: 'r1' })
  expect(hook.result.current).toEqual([])
  const next = streams[1]
  next.emit('retrospective.batch_started', { sequence: 1, run_id: 'r2' })
  expect(hook.result.current).toHaveLength(1)
  next.emit('stream.reset', {})
  next.emit('retrospective.batch_started', { sequence: 1, run_id: 'r2' })
  expect(hook.result.current).toHaveLength(1)
  next.emit('done', {})
  expect(next.close).toHaveBeenCalled()
})

it('opens no stream before the current run identity is known', () => {
  vi.stubGlobal('EventSource', FakeEventSource)
  renderHook(() => useBsEventStream(4, null, { retrospective: true }))
  expect(streams).toHaveLength(0)
})
