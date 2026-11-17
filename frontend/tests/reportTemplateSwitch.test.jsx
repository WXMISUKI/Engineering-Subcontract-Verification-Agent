import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import BlockchainSupervisionWorkbench from '../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench'
import { getBsReport, getBsOpinion, previewBsOpinion, downloadBsOpinion, saveBlob } from '../src/plus-ui/blockchain-supervision/lib/api'

const readiness = { schema_version: 'bs.report-readiness.v1', draft_readable: true, confirmed_output_ready: true,
  task_close_ready: true, execution: { state: 'completed' }, reading_quality: { state: 'complete', pages: [] },
  finding_review: { blocking: [] }, blocking_reasons: [], next_actions: [] }
const snapshot = { review_run_id: 'template-switch-run', steps: [], runs: [] }
const legacy = { meta: {}, summary: {}, sections: [], reference_snapshot: snapshot,
  business_document: { basic_information: {}, sections: [], conclusion: {} } }
const document = { title: '新模板正文', blocks: [{ type: 'paragraph', text: '单主体 V1.3 正文' }] }
const current = { ...legacy, template_document: document }

vi.mock('../src/plus-ui/PlusUIShell', () => ({ default: ({ children }) => <div>{children}</div> }))
vi.mock('../src/components/ModelSelect', () => ({ default: () => null }))
vi.mock('../src/plus-ui/blockchain-supervision/lib/api', async (original) => ({
  ...await original(),
  listBsTasks: vi.fn(async () => ({ tasks: [{ task_id: 7, state: 'done' }] })),
  getBsTask: vi.fn(async () => ({ task_id: 7, title: '模板切换测试', state: 'done', scope: {}, report_readiness: readiness })),
  listBsFindings: vi.fn(async () => ({ findings: [], reference_snapshot: snapshot })),
  getBsTodo: vi.fn(async () => ({})), getBsRuleset: vi.fn(async () => ({})),
  getBsTaskUsage: vi.fn(async () => ({})), pingBs: vi.fn(async () => ({ ok: true })),
  getBsReport: vi.fn(), getBsOpinion: vi.fn(), previewBsOpinion: vi.fn(), downloadBsOpinion: vi.fn(), saveBlob: vi.fn(),
}))

afterEach(() => { cleanup(); sessionStorage.clear(); history.replaceState({}, '', '/'); vi.unstubAllGlobals(); vi.resetAllMocks() })

it('discards a delayed report and delayed export after switching templates', async () => {
  history.replaceState({}, '', '/?bs_view=report')
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  let resolveOld, resolveExport
  getBsReport.mockImplementation((_id, { templateId }) => templateId === 'v1.3' ? Promise.resolve(current) : new Promise((resolve) => { resolveOld = resolve }))
  getBsOpinion.mockResolvedValue({ ...current, opinion_digest: 'v13-digest' })
  previewBsOpinion.mockResolvedValue({ markdown: '新预览', digest: 'v13-digest' })
  downloadBsOpinion.mockImplementation(() => new Promise((resolve) => { resolveExport = resolve }))
  render(<BlockchainSupervisionWorkbench conversationId="template-switch" initialTaskId={7} />)
  const select = await screen.findByLabelText('报告模板')
  await waitFor(() => expect(resolveOld).toBeTypeOf('function'))
  fireEvent.change(select, { target: { value: 'v1.3' } })
  await screen.findByText('单主体 V1.3 正文')
  await act(async () => resolveOld(legacy))
  expect(screen.getByText('单主体 V1.3 正文')).toBeTruthy()
  fireEvent.click(screen.getByTestId('generate-formal-opinion'))
  await screen.findByTestId('opinion-export-word')
  expect(getBsOpinion).toHaveBeenCalledWith(7, { stage: 'confirmed', templateId: 'v1.3' })
  expect(previewBsOpinion).toHaveBeenCalledWith(7, 'confirmed', { templateId: 'v1.3' })
  fireEvent.click(screen.getByTestId('opinion-export-word'))
  await waitFor(() => expect(resolveExport).toBeTypeOf('function'))
  expect(downloadBsOpinion).toHaveBeenCalledWith(7, 'docx', 'confirmed', { expectedDigest: 'v13-digest', templateId: 'v1.3' })
  getBsReport.mockResolvedValue(legacy)
  fireEvent.change(select, { target: { value: 'legacy' } })
  await act(async () => resolveExport(new Blob(['old export'])))
  expect(saveBlob).not.toHaveBeenCalled()
  expect(screen.queryByTestId('report-template-v13')).toBeNull()
  expect(screen.queryByTestId('opinion-export-word')).toBeNull()
})
