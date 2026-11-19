import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import RetrospectiveWorkspace from '../src/plus-ui/blockchain-supervision/RetrospectiveWorkspace'

const api = vi.hoisted(() => ({
  getBsTask: vi.fn(), getBsRetrospective: vi.fn(), downloadBsReport: vi.fn(), saveBlob: vi.fn(),
  createBsTask: vi.fn(), deleteBsMaterial: vi.fn(), startBsRetrospective: vi.fn(), uploadBsMaterial: vi.fn(),
  associateBsGeneralSourceSet: vi.fn(), removeBsGeneralSourceSetMember: vi.fn(), saveBsRetrospectiveContracts: vi.fn(),
  setBsMaterialRole: vi.fn(), patchBsSection: vi.fn(),
}))

vi.mock('../src/plus-ui/blockchain-supervision/lib/api', async (importOriginal) => ({ ...(await importOriginal()), ...api }))

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('RetrospectiveWorkspace', () => {
  it('shows saved findings and source-page navigation without admission review controls', async () => {
    api.getBsTask.mockResolvedValue({ materials: [] })
    api.getBsRetrospective.mockResolvedValue({ state: 'completed', report: {
      execution_status: 'completed', summary: '列示1项合同文本问题或风险提示。', scope_note: '单合同范围',
      limitations: ['第24页未取得可靠正文'], rows: [{ finding_id: 'f1', cells: ['项目', '分包', '合同.pdf', 'PDF第39页 · 12.1',
        '合同原文摘录', '内部歧义', '合同内部约定', '对照原文'],
      evidence: [{ material_id: 7, page: 39, clause: '12.1' }] }],
    } })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await waitFor(() => expect(screen.getByText('内部歧义')).toBeTruthy())
    expect(screen.getByRole('link', { name: 'PDF第39页 · 12.1' }).getAttribute('href')).toContain('page=39')
    expect(screen.queryByText(/人工复核|确认问题|生成报告/)).toBeNull()
    expect(screen.getByRole('button', { name: /Word/ })).toBeTruthy()
    expect(screen.queryByText(/核查限制|未取得可靠正文/)).toBeNull()
  })

  it('reuses the upload page and blocks a restored single contract', async () => {
    api.getBsTask.mockResolvedValue({ state: 'setup', materials: [{ material_id: 7, role: 'sub', file_name: '分包合同.pdf' }],
      validation: { hard_ok: false, hard_gaps: ['请上传总包合同PDF'], contract_relations: [{ material_id: 7, relation: 'main' }] } })
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await screen.findByText('分包合同.pdf')
    expect(screen.getByTestId('prototype-upload-page')).toBeTruthy()
    const start = screen.getByRole('button', { name: '开始核查' })
    expect(start.disabled).toBe(true)
    fireEvent.click(start)
    expect(api.startBsRetrospective).not.toHaveBeenCalled()
    expect(screen.queryByText(/五步核查|报审资料包/)).toBeNull()
    expect(screen.getByTestId('upload-category').textContent).not.toContain('报审资料包')
  })

  it('starts restored dual contracts through the retrospective API only', async () => {
    const relations = [{ material_id: 6, relation: 'main' }, { material_id: 7, relation: 'main' }]
    api.getBsTask.mockResolvedValue({ state: 'setup', materials: [
      { material_id: 6, role: 'general', file_name: '总包合同.pdf' },
      { material_id: 7, role: 'sub', file_name: '分包合同.pdf' },
    ], validation: { hard_ok: true, hard_gaps: [], contract_relations: relations } })
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    api.startBsRetrospective.mockResolvedValue({ state: 'queued', report: null })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await screen.findByText('分包合同.pdf')
    fireEvent.click(screen.getByRole('button', { name: '开始核查' }))
    await waitFor(() => expect(api.startBsRetrospective).toHaveBeenCalledWith(4, relations))
    expect(screen.queryByText(/人工复核/)).toBeNull()
  })

  it('persists relationship changes and restores them from the server after remount', async () => {
    let detail = { state: 'setup', materials: [
      { material_id: 6, role: 'general', file_name: '总包合同.pdf' },
      { material_id: 7, role: 'sub', file_name: '合同.pdf' },
      { material_id: 8, role: 'sub', file_name: '附件.pdf' },
    ], validation: { hard_ok: false, hard_gaps: ['请明确一份分包合同正文'], contract_relations: [{ material_id: 6, relation: 'main' }] } }
    api.getBsTask.mockImplementation(async () => detail)
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    api.saveBsRetrospectiveContracts.mockImplementation(async (id, relations) => {
      detail = { ...detail, validation: { hard_ok: relations.length === 3, hard_gaps: [], contract_relations: relations } }
      return detail.validation
    })
    const view = render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    const main = await screen.findByLabelText('合同.pdf的合同关系')
    fireEvent.change(main, { target: { value: 'main' } })
    await waitFor(() => expect(main.disabled).toBe(false))
    fireEvent.change(screen.getByLabelText('附件.pdf的合同关系'), { target: { value: 'attachment' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '开始核查' }).disabled).toBe(false))
    expect(api.saveBsRetrospectiveContracts).toHaveBeenCalledTimes(2)
    view.unmount()
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await waitFor(() => expect(screen.getByLabelText('附件.pdf的合同关系').value).toBe('attachment'))
    expect(screen.getByLabelText('合同.pdf的合同关系').value).toBe('main')
  })

  it('uploads with the selected general role and keeps files after a later upload fails', async () => {
    api.createBsTask.mockResolvedValue({ task_id: 5 })
    api.getBsTask.mockResolvedValue({ state: 'setup', materials: [{ material_id: 6, role: 'general', file_name: '总包合同.pdf' }],
      validation: { hard_ok: false, hard_gaps: ['请上传分包合同PDF'] } })
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    api.uploadBsMaterial.mockResolvedValueOnce({ material_id: 6 }).mockRejectedValueOnce(new Error('上传失败'))
    const created = vi.fn()
    render(<RetrospectiveWorkspace conversationId={2} onTaskCreated={created}/>)
    fireEvent.change(screen.getByLabelText('上传合同PDF'), { target: { files: [
      new File(['pdf'], '总包合同.pdf', { type: 'application/pdf' }), new File(['pdf'], '附件.pdf', { type: 'application/pdf' }),
    ] } })
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('已上传的文件会保留'))
    expect(created).toHaveBeenCalledWith(5)
    expect(api.uploadBsMaterial.mock.calls[0][2]).toMatchObject({ role: 'general', materialCategory: 'general' })
  })

  it('shows a failed execution without a clean-report statement or export actions', async () => {
    api.getBsTask.mockResolvedValue({ state: 'failed', materials: [] })
    api.getBsRetrospective.mockResolvedValue({ state: 'failed', report: { execution_status: 'failed',
      summary: '本次合同倒查未完成，尚不能形成风险检查结论。', scope_note: '单合同范围', rows: [], limitations: ['模型超时'] } })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await screen.findByText('本次合同倒查未完成，尚不能形成风险检查结论。')
    expect(screen.queryByRole('button', { name: /Word|PDF/ })).toBeNull()
    expect(screen.queryByText(/未发现可列示问题/)).toBeNull()
  })

  it('shows the direct contract review stage instead of admission workflow steps', async () => {
    api.getBsTask.mockResolvedValue({ materials: [] })
    api.getBsRetrospective.mockResolvedValue({ state: 'running', report: null,
      progress: { phase: 'coverage', label: '逐条核对冻结规范与合同', index: 7, total: 18 } })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    expect(await screen.findByText('逐条核对冻结规范与合同 · 7/18批')).toBeTruthy()
  })

  it('uses three shared stages and keeps accepted materials read only when navigating back', async () => {
    api.getBsTask.mockResolvedValue({ state: 'running', materials: [
      { material_id: 6, role: 'general', file_name: '总包合同.pdf' },
      { material_id: 7, role: 'sub', file_name: '分包合同.pdf' },
    ], validation: { contract_relations: [{ material_id: 6, relation: 'main' }, { material_id: 7, relation: 'main' }] } })
    api.getBsRetrospective.mockResolvedValue({ state: 'running', report: null })
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await screen.findByText('合同智能核查')
    const flow = within(screen.getByTestId('prototype-flow-header'))
    expect(flow.getAllByRole('button')).toHaveLength(3)
    expect(flow.getByRole('button', { name: /审查报告/ }).disabled).toBe(true)
    fireEvent.click(flow.getByRole('button', { name: /上传资料/ }))
    await screen.findByText('材料已锁定为查看态')
    expect(screen.getByLabelText('总包合同.pdf的合同关系').disabled).toBe(true)
    expect(screen.queryByTestId('materials-edit')).toBeNull()
    expect(screen.queryByText('开始核查')).toBeNull()
    fireEvent.click(screen.getByTestId('materials-back'))
    expect(screen.getByText('合同智能核查')).toBeTruthy()
    expect(api.startBsRetrospective).not.toHaveBeenCalled()
  })

  it('keeps project text editable until blur and restores saved metadata', async () => {
    let detail = { state: 'setup', materials: [], section: { project_name: '旧名称' },
      validation: { hard_ok: false, hard_gaps: ['请上传总包合同与分包合同'] } }
    api.getBsTask.mockImplementation(async () => detail)
    api.getBsRetrospective.mockResolvedValue({ state: 'not_started', report: null })
    api.patchBsSection.mockImplementation(async (id, fields) => {
      detail = { ...detail, section: { project_name: fields.projectName } }
    })
    const view = render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    const input = await screen.findByLabelText('项目名称')
    await waitFor(() => expect(input.value).toBe('旧名称'))
    fireEvent.change(input, { target: { value: '嘉' } })
    fireEvent.change(input, { target: { value: '嘉松工程' } })
    expect(input.disabled).toBe(false)
    expect(api.patchBsSection).not.toHaveBeenCalled()
    fireEvent.blur(input)
    await waitFor(() => expect(api.patchBsSection).toHaveBeenCalledWith(4, { projectName: '嘉松工程' }))
    await waitFor(() => expect(input.disabled).toBe(false))
    view.unmount()
    render(<RetrospectiveWorkspace taskId={4} conversationId={2}/>)
    await waitFor(() => expect(screen.getByLabelText('项目名称').value).toBe('嘉松工程'))
  })

  it('rejects empty PDFs before creating a task', () => {
    render(<RetrospectiveWorkspace conversationId={2} onTaskCreated={vi.fn()}/>)
    fireEvent.change(screen.getByLabelText('上传合同PDF'), { target: { files: [new File([], '空.pdf')] } })
    expect(screen.getByRole('alert').textContent).toContain('非空的合同PDF')
    expect(api.createBsTask).not.toHaveBeenCalled()
    expect(api.uploadBsMaterial).not.toHaveBeenCalled()
  })

})
