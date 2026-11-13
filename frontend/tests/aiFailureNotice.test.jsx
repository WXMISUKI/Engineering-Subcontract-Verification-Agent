import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import BlockchainSupervisionWorkbench from '../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench'
import { pingBs, runBsCheck, listBsFindings } from '../src/plus-ui/blockchain-supervision/lib/api'

const snapshot = { review_run_id: 'run-notice', ai_auto_enabled: true, ai_auto_groups: ['signature_chain'],
  steps: [], runs: [{ group_id: 'completeness', state: 'completed' }, { group_id: 'signature_chain', state: 'completed' }] }

vi.mock('../src/plus-ui/PlusUIShell', () => ({ default: ({ children }) => <div>{children}</div> }))
vi.mock('../src/components/ModelSelect', () => ({ default: () => null }))
vi.mock('../src/plus-ui/blockchain-supervision/lib/api', async (original) => ({
  ...await original(),
  listBsTasks: vi.fn(async () => ({ tasks: [{ task_id: 7, state: 'running' }] })),
  getBsTask: vi.fn(async () => ({ task_id: 7, state: 'running', scope: { A3: true }, progress: { phase: 'ai_checking' } })),
  listBsFindings: vi.fn(async () => ({ findings: [], reference_snapshot: snapshot })),
  getBsTodo: vi.fn(async () => ({})), getBsRuleset: vi.fn(async () => ({})),
  getBsTaskUsage: vi.fn(async () => ({})), pingBs: vi.fn(async () => ({ ok: true })),
  runBsCheck: vi.fn(async () => ({})),
}))
vi.mock('../src/plus-ui/blockchain-supervision/lib/autoAiReview', async (original) => ({
  ...await original(),
  runBackgroundAiReview: vi.fn(async () => ({ state: 'failed', failureCategories: ['invalid_response'] })),
}))

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.clearAllMocks(); runBsCheck.mockResolvedValue({}); listBsFindings.mockResolvedValue({ findings: [], reference_snapshot: snapshot }) })

it('AI 核查失败只在核查区展示一次，并保留重试入口', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  render(<BlockchainSupervisionWorkbench conversationId="notice-test" initialTaskId={7} />)
  await waitFor(() => expect(screen.getByTestId('check-run-error').textContent).toContain('模型返回格式无效'))
  expect(screen.getAllByText(/模型返回格式无效/)).toHaveLength(1)
  expect(screen.getByRole('button', { name: '重试核查' })).toBeTruthy()
})

it('AI 失败不隐藏独立的服务健康错误', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  pingBs.mockResolvedValueOnce({ ok: false })
  render(<BlockchainSupervisionWorkbench conversationId="notice-test" initialTaskId={7} />)
  await waitFor(() => expect(screen.getByTestId('check-run-error').textContent).toContain('模型返回格式无效'))
  expect(screen.getByText(/智能体服务未就绪/)).toBeTruthy()
  expect(screen.getAllByText(/模型返回格式无效/)).toHaveLength(1)
})

it('规则核查失败也只在核查区展示一次', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  runBsCheck.mockRejectedValue(new Error('规则核查暂不可用'))
  render(<BlockchainSupervisionWorkbench conversationId="notice-test" initialTaskId={7} />)
  await waitFor(() => expect(screen.getByTestId('check-run-error').textContent).toContain('规则核查暂不可用'))
  expect(screen.getAllByText(/规则核查暂不可用/)).toHaveLength(1)
  expect(screen.getByRole('button', { name: '重试核查' })).toBeTruthy()
})

it('自动核查中读取结果失败只显示一处具体原因', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  listBsFindings.mockResolvedValueOnce({ findings: [], reference_snapshot: snapshot })
    .mockRejectedValue(new Error('核查结果服务暂不可用'))
  render(<BlockchainSupervisionWorkbench conversationId="notice-test" initialTaskId={7} />)
  await waitFor(() => expect(screen.getByTestId('check-run-error').textContent).toContain('核查结果服务暂不可用'))
  expect(screen.getAllByText(/核查结果服务暂不可用/)).toHaveLength(1)
  expect(screen.getByRole('button', { name: '重试核查' })).toBeTruthy()
})
