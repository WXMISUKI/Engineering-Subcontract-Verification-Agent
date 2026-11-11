import { describe, expect, it } from 'vitest'
import { taskMutationBusy } from '../src/plus-ui/blockchain-supervision/lib/taskMutationGate'

describe('taskMutationBusy', () => {
  it('服务端 preparing 或未完成 running 任务阻断写操作', () => {
    expect(taskMutationBusy({ taskState: 'preparing' })).toBe(true)
    expect(taskMutationBusy({ taskState: 'running', steps: [] })).toBe(true)
    expect(taskMutationBusy({ taskState: 'running', steps: [{ state: 'running' }] })).toBe(true)
  })

  it('五步完成且没有活动阶段时允许人工重核查', () => {
    expect(taskMutationBusy({
      taskState: 'running',
      steps: [{ state: 'completed' }, { state: 'out_of_scope' }],
      progressPhase: 'completed',
    })).toBe(false)
  })

  it('本地编排和后台阶段活动时始终阻断', () => {
    expect(taskMutationBusy({ taskState: 'running', steps: [], localPhase: 'running' })).toBe(true)
    expect(taskMutationBusy({ taskState: 'running', steps: [{ state: 'completed' }], progressPhase: 'ai_checking' })).toBe(true)
  })

  it('浏览器编排已停止的 running 任务允许用户重新核查', () => {
    expect(taskMutationBusy({
      taskState: 'running',
      steps: [],
      allowStaleRunning: true,
    })).toBe(false)
  })
})
