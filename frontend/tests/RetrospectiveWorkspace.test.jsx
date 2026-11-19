import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import RetrospectiveWorkspace from '../src/plus-ui/blockchain-supervision/RetrospectiveWorkspace'

const api = vi.hoisted(() => ({
  getBsTask: vi.fn(), getBsRetrospective: vi.fn(), downloadBsReport: vi.fn(), saveBlob: vi.fn(),
  createBsTask: vi.fn(), deleteBsMaterial: vi.fn(), startBsRetrospective: vi.fn(), uploadBsMaterial: vi.fn(),
}))

vi.mock('../src/plus-ui/blockchain-supervision/lib/api', () => api)

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('RetrospectiveWorkspace', () => {
  it('shows saved findings and source-page navigation without admission review controls', async () => {
    api.getBsTask.mockResolvedValue({ materials: [] })
    api.getBsRetrospective.mockResolvedValue({ state: 'completed', report: {
      execution_status: 'completed', summary: '列示1项合同文本问题或风险提示。', scope_note: '单合同范围',
      limitations: [], rows: [{ finding_id: 'f1', cells: ['项目', '分包', '合同.pdf', 'PDF第39页 · 12.1',
        '合同原文摘录', '内部歧义', '合同内部约定', '对照原文'],
      evidence: [{ material_id: 7, page: 39, clause: '12.1' }] }],
    } })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await waitFor(() => expect(screen.getByText('内部歧义')).toBeTruthy())
    expect(screen.getByRole('link', { name: 'PDF第39页 · 12.1' }).getAttribute('href')).toContain('page=39')
    expect(screen.queryByText(/人工复核|确认问题|生成报告/)).toBeNull()
    expect(screen.getByRole('button', { name: /Word/ })).toBeTruthy()
  })

  it('starts a restored single contract directly with no admission confirmations', async () => {
    api.getBsTask.mockResolvedValue({ state: 'setup', materials: [{ material_id: 7, role: 'sub', file_name: '分包合同.pdf' }] })
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    api.startBsRetrospective.mockResolvedValue({ state: 'queued', report: null })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    const start = await screen.findByRole('button', { name: '开始合同倒查' })
    expect(start.disabled).toBe(false)
    fireEvent.click(start)
    await waitFor(() => expect(api.startBsRetrospective).toHaveBeenCalledWith(4, [{ material_id: 7, relation: 'main' }]))
  })

  it('requires explicit file relationships when several contracts are present', async () => {
    api.getBsTask.mockResolvedValue({ state: 'setup', materials: [
      { material_id: 7, role: 'sub', file_name: '合同.pdf' },
      { material_id: 8, role: 'sub', file_name: '附件.pdf' },
    ] })
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    const start = await screen.findByRole('button', { name: '开始合同倒查' })
    expect(start.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('合同.pdf的合同关系'), { target: { value: 'main' } })
    fireEvent.change(screen.getByLabelText('附件.pdf的合同关系'), { target: { value: 'attachment' } })
    expect(start.disabled).toBe(false)
  })

  it('shows a failed execution without a clean-report statement or export actions', async () => {
    api.getBsTask.mockResolvedValue({ state: 'failed', materials: [] })
    api.getBsRetrospective.mockResolvedValue({ state: 'failed', report: { execution_status: 'failed',
      summary: '本次合同倒查未完成，尚不能形成风险检查结论。', scope_note: '单合同范围', rows: [], limitations: ['模型超时'] } })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await screen.findByText('模型超时')
    expect(screen.queryByRole('button', { name: /Word|PDF/ })).toBeNull()
    expect(screen.queryByText(/未发现可列示问题/)).toBeNull()
  })
})
