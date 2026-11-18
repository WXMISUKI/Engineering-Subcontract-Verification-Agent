import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'
import { getBsReport, getBsOpinion, downloadBsReport, downloadBsOpinion, previewBsOpinion } from '../src/plus-ui/blockchain-supervision/lib/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const document = { title: '测试施工专业分包审查意见（AI初审）', blocks: [
  { type: 'heading', text: '三、审查情况' },
  { type: 'table', headers: ['核查维度', '核查情况', '判定'], widths: [.2, .6, .2], rows: [['设备与资源', '本版本未覆盖', '待核实']] },
  { type: 'paragraph', text: '人工审批意见：____________' },
] }
const report = { meta: {}, summary: {}, sections: [], business_document: { basic_information: {}, sections: [], conclusion: {} }, template_document: document }

it('keeps the default legacy option and permits switching even without a report', () => {
  const change = vi.fn()
  render(<ReportView report={null} prototypeMode onTemplateChange={change} />)
  expect(screen.getByLabelText('报告模板').value).toBe('legacy')
  fireEvent.change(screen.getByLabelText('报告模板'), { target: { value: 'v1.3' } })
  expect(change).toHaveBeenCalledWith('v1.3')
})

it('shows the V1.3 tree and preserves the existing export gate', () => {
  render(<ReportView report={report} templateId="v1.3" prototypeMode onTemplateChange={vi.fn()} />)
  expect(screen.getByTestId('report-template-v13').textContent).toContain('本版本未覆盖')
  expect(screen.getByTestId('report-template-v13').textContent).toContain('人工审批意见：____________')
  expect(screen.queryByTestId('business-document')).toBeNull()
  expect(screen.queryByTestId('prototype-report-exports')).toBeNull()
  expect(screen.queryByTestId('opinion-export-word')).toBeNull()
})

it('uses the explicitly generated confirmed template for the formal preview', () => {
  render(<ReportView report={report} opinion={{ template_document: { ...document, title: '已生成确认稿' } }} prototypeMode />)
  expect(screen.getByTestId('report-template-v13').textContent).toContain('已生成确认稿')
  expect(screen.queryByText(document.title)).toBeNull()
})

it('shows only formal opinion exports when V1.3 report and opinion share one document', () => {
  render(<ReportView report={report} opinion={{ opinion_digest: 'digest', template_document: document }}
    templateId="v1.3" prototypeMode onExportOpinion={vi.fn()} onExport={vi.fn()} />)
  expect(screen.queryByTestId('prototype-report-exports')).toBeNull()
  expect(screen.getByTestId('opinion-export-word')).toBeTruthy()
  expect(screen.getByTestId('opinion-export-pdf')).toBeTruthy()
})

it('sends the same template to read, preview and export while default URLs stay compatible', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ markdown: 'preview', opinion_digest: 'fingerprint' }), blob: async () => new Blob(['test']) })
  vi.stubGlobal('fetch', fetch)
  await getBsReport(1)
  expect(fetch.mock.calls[0][0]).toMatch(/\/report\?stage=draft$/)
  await getBsReport(1, { templateId: 'v1.3' })
  await getBsOpinion(1, { templateId: 'v1.3', stage: 'confirmed' })
  await previewBsOpinion(1, 'confirmed', { templateId: 'v1.3' })
  await downloadBsReport(1, 'docx', 'confirmed', { templateId: 'v1.3' })
  await downloadBsOpinion(1, 'pdf', 'confirmed', { templateId: 'v1.3', expectedDigest: 'fingerprint' })
  for (const [url] of fetch.mock.calls.slice(1)) expect(url).toContain('template_id=v1.3')
  expect(fetch.mock.calls.at(-1)[0]).toContain('expected_digest=fingerprint')
  await downloadBsReport(1, 'docx', 'draft', { versionNo: 2 })
  expect(fetch.mock.calls.at(-1)[0]).toMatch(/version_no=2$/)
})
