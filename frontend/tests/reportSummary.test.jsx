import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'

afterEach(() => cleanup())

function report() {
  return {
    meta: { title: '摘要测试任务', baseline_date: '2026-11-08', state: 'running' },
    summary: { headline: '本轮核查尚未形成完整结论。', counts: { hit: 1, miss: 2, undecidable: 1 }, total: 4 },
    sections: [],
    materials: [],
    qualification_summary: {
      meta: { state: 'running' },
      subjects: [{ subject_id: 7, name: '测试劳务单位' }],
      general_contractor: { status: 'unresolved', label: '承包单位待核实', candidate_company: '候选总包' },
      counts: { hit: 1, miss: 2, undecidable: 1, total: 4 },
      sections: {
        qualification_compliance: [],
        non_compliant: [{ item_id: 'Q-1', label: '资质匹配', evidence: { page: 3 } }],
        missing: [{ item_id: 'M-1', label: '总包合同' }],
        undecidable: [{ item_id: 'U-1', label: '承包关系' }],
        key_evidence: [],
      },
      limitations: ['OCR 第 20 页需人工核对'],
      publication: { state: 'candidate' },
      audit_ref: { machine_sections: 'sections' },
    },
  }
}

it('renders the qualification summary and keeps candidate status visible', () => {
  render(<ReportView report={report()} prototypeMode />)
  expect(screen.getByTestId('qualification-summary')).toBeTruthy()
  expect(screen.getByTestId('qualification-summary-contractor').textContent).toContain('候选总包')
  expect(screen.getByTestId('qualification-summary-publication').textContent).toBe('candidate')
  expect(screen.getByTestId('qualification-summary-non_compliant').textContent).toContain('第 3 页')
  expect(screen.getByTestId('qualification-summary-limitations').textContent).toContain('OCR 第 20 页')
})

it('renders the same summary in the non-prototype report view', () => {
  render(<ReportView report={report()} />)
  expect(screen.getByTestId('qualification-summary')).toBeTruthy()
  expect(screen.getByTestId('qualification-summary-subjects').textContent).toContain('测试劳务单位')
})

it('discloses rule-level scope limitations in the formal report view', () => {
  const data = report()
  data.scope_statements = ['规则 T15-01 本轮未覆盖，未形成符合或不符合结论。']
  render(<ReportView report={data} />)

  expect(screen.getByTestId('report-scope-statements').textContent)
    .toContain('规则 T15-01 本轮未覆盖')
})

it('shows only Word and PDF exports for the confirmed report and keeps its gate', () => {
  const onExport = vi.fn()
  const view = render(<ReportView report={report()} prototypeMode onExport={onExport} blockingByStage={{ confirmed: { message: '尚有问题待复核' } }} />)
  expect(screen.queryByTestId('prototype-report-export-draft-docx')).toBeNull()
  expect(screen.getByTestId('prototype-report-export-word').disabled).toBe(true)
  expect(screen.getByTestId('prototype-report-export-pdf').disabled).toBe(true)
  expect(onExport).not.toHaveBeenCalled()
  view.rerender(<ReportView report={report()} prototypeMode onExport={onExport} />)
  fireEvent.click(screen.getByTestId('prototype-report-export-pdf'))
  expect(onExport).toHaveBeenCalledWith('confirmed', 'pdf')
})

it('offers regeneration while retaining the current formal opinion during a refresh', () => {
  const onGenerateOpinion = vi.fn()
  render(<ReportView report={report()} prototypeMode opinion={{ opinion_digest: 'current' }} onGenerateOpinion={onGenerateOpinion} />)
  fireEvent.click(screen.getByTestId('regenerate-formal-opinion'))
  expect(onGenerateOpinion).toHaveBeenCalledOnce()
  expect(screen.getByTestId('opinion-export-word')).toBeTruthy()
  expect(screen.getByTestId('opinion-export-pdf')).toBeTruthy()
})

it('renders business report content without showing ignored audit items', () => {
  const data = report()
  data.ignored_items = [{ item_id: 'old', label: '不展示的忽略项' }]
  data.business_document = {
    basic_information: { '施工总承包单位': '示例总包' },
    sections: [{ title: '要素完整性核查', summary: '有事项待核实。', fields: [{ label: '主体资料', summary: '需核实材料。' }] }],
    issues: [{ item_id: 'new', category: '要素完整性核查', label: '营业执照', message: '需核实。' }],
    conclusion: { text: '尚有事项待核实。', limitations: [] },
  }
  render(<ReportView report={data} />)
  expect(screen.getByTestId('business-document').textContent).toContain('示例总包')
  expect(screen.getByTestId('business-document').textContent).toContain('营业执照')
  expect(screen.getByTestId('business-document').textContent).toContain('营业执照需要核实')
  expect(screen.queryByText('不展示的忽略项')).toBeNull()
  expect(screen.queryByTestId('report-ignored-items')).toBeNull()
})

it('shows uncovered rules and pages in the business report', () => {
  const data = report()
  data.business_document = { basic_information: {}, sections: [], issues: [],
    scope_limitations: ['负面清单 AI 辅助候选未覆盖规则 NL-B-01；材料「报审.pdf」第 63 页识别不完整。'],
    conclusion: { text: '本次核查不构成完整通过。', limitations: [] } }
  render(<ReportView report={data} />)
  const content = screen.getByTestId('business-document').textContent
  expect(content).toContain('NL-B-01')
  expect(content).toContain('第 63 页')
  expect(content).toContain('不构成完整通过')
})

it('renders projected themes and human notes instead of raw issue text', () => {
  const data = report()
  data.business_document = {
    basic_information: {}, sections: [],
    issues: [{ category: '人员核查', label: '原始事项', message: '不应展开的原始长文' }],
    pending_summaries: [{ category: '人员核查', status: '待核实', text: '待核实：人员资料。' }],
    human_notes: [{ category: '人员核查', label: '证件', note: '已联系补充材料' }],
    conclusion: { text: '尚需核实', limitations: [] },
  }
  render(<ReportView report={data} />)
  const text = screen.getByTestId('business-document').textContent
  expect(text).toContain('人员资料需要核实')
  expect(text).toContain('人工补充：已联系补充材料')
  expect(text).not.toContain('不应展开的原始长文')
})

it('renders projected review key points and grouped limitations', () => {
  const data = report()
  data.business_document = {
    basic_information: {},
    sections: [{ title: '资质审查', summary: '不应展示的阶段结论', fields: [{ label: '旧字段', summary: '旧内容' }],
      key_points: [{ label: '事项定性', summary: '事项性质待核实', status: '待核实' }, { label: '负面清单', summary: '结果待核实', status: '待核实' }] }],
    issues: [], pending_summaries: [], human_notes: [],
    conclusion: { text: '不应展示的总体结论', limitations: ['总包合同未提供', '未核查金额占比'],
      limitation_groups: [{ label: '依据与合同范围', items: ['总包合同未提供', '未核查金额占比'] }] },
  }
  render(<ReportView report={data} />)
  const text = screen.getByTestId('business-document').textContent
  expect(text).toContain('事项定性')
  expect(text).toContain('负面清单')
  expect(text).not.toContain('依据与合同范围')
  expect(text).toContain('未核查金额占比')
  expect(text).not.toContain('五、核查说明')
  expect(screen.queryByRole('columnheader', { name: '状态' })).toBeNull()
  expect(text).not.toContain('旧字段')
  expect(text).not.toContain('不应展示的阶段结论')
  expect(text).not.toContain('不应展示的总体结论')
})

it('shows the basis and grounded verdict beside each current review point', () => {
  const data = report()
  data.business_document = { schema_version: 'bs.business-report-document.v2', basic_information: {},
    sections: [{ title: '内部记录核查', key_points: [{ label: '信用记录',
      basis: ['内部信用管理办法第六条'], summary: '已查阅信用记录。', verdict: '符合' }] }],
    conclusion: { limitations: [] }, pending_summaries: [{ category: '不展示的章节', text: '不应展示' }] }
  render(<ReportView report={data} />)
  const text = screen.getByTestId('business-document').textContent
  expect(text).toContain('内部信用管理办法第六条')
  expect(text).toContain('已查阅信用记录')
  expect(screen.getByRole('columnheader', { name: '判定' })).toBeTruthy()
  expect(screen.getByRole('columnheader', { name: '判定' }).className).toContain('bg-[#173A64]')
  expect(screen.getByText('信用记录').className).toContain('font-semibold')
  expect(text).toContain('符合')
  expect(text).not.toContain('待处理事项摘要')
  expect(text).not.toContain('不应展示')
})

it('uses the same business themes for legacy snapshots as exports', () => {
  const data = report()
  data.business_document = {
    basic_information: {}, sections: [],
    issues: [
      { category: '资质核查', label: '规则一', summary_theme: '分包内容', status: 'undecidable' },
      { category: '资质核查', label: '规则二', summary_theme: '分包内容', status: 'undecidable' },
      { category: '资质核查', label: '主体材料', status: 'hit', material_missing: true, missing: { labels: ['营业执照'] } },
      { category: '资质核查', label: '人工意见', human_disposition: { code: 'accepted', note: '补充说明' } },
      { category: '资质核查', label: '忽略事项', human_disposition: { code: 'ignored' } },
    ],
    conclusion: { text: '待核实', limitations: [] },
  }
  render(<ReportView report={data} />)
  const text = screen.getByTestId('business-document').textContent
  expect(text).toContain('分包内容需要核实。')
  expect(text).toContain('营业执照材料缺失。')
  expect(text).toContain('人工补充：补充说明')
  expect(text).not.toContain('规则一')
  expect(text).not.toContain('规则二')
  expect(text).not.toContain('忽略事项')
})

it('shows basic information without source links', () => {
  const data = report()
  data.basic_information = { general_contractor: { evidence: [{ material_id: 7, page: 1 }] } }
  data.business_document = { basic_information: { '施工总承包单位': '示例总包' }, sections: [], issues: [], conclusion: {} }
  const onOpenSource = vi.fn()
  render(<ReportView report={data} onOpenSource={onOpenSource} />)
  expect(screen.getByText('示例总包')).toBeTruthy()
  expect(screen.queryByText('查看原文')).toBeNull()
  expect(onOpenSource).not.toHaveBeenCalled()
})

it('marks a conflicting value and shows source candidates inline', () => {
  const data = report()
  data.basic_information = { general_contractor: { evidence: [{ material_id: 5 }, { material_id: 7, page: 2 }] } }
  data.business_document = { basic_information: { '施工总承包单位': '候选总包' },
    basic_information_status: { '施工总承包单位': 'conflict' }, sections: [], issues: [], conclusion: {} }
  const onOpenSource = vi.fn()
  render(<ReportView report={data} onOpenSource={onOpenSource} />)
  expect(screen.getByText('候选总包（待核实）')).toBeTruthy()
  expect(screen.queryByText('查看原文')).toBeNull()
  expect(onOpenSource).not.toHaveBeenCalled()
})

it('lists conflicting source values without exposing original-source controls', () => {
  const data = report()
  data.basic_information = { general_contractor: { evidence: [
    { material_id: 7, page: 1, file_name: '报审表.pdf', value: '甲公司' },
    { material_id: 8, page: 4, file_name: '合同.pdf', value: '乙公司' },
  ] } }
  data.business_document = { basic_information: { '施工总承包单位': null },
    basic_information_status: { '施工总承包单位': 'conflict' }, sections: [], issues: [], conclusion: {} }
  const onOpenSource = vi.fn()
  render(<ReportView report={data} onOpenSource={onOpenSource} />)
  expect(screen.getByText(/甲公司/)).toBeTruthy()
  expect(screen.getByText(/乙公司/)).toBeTruthy()
  expect(screen.queryByText(/查看原文/)).toBeNull()
  expect(onOpenSource).not.toHaveBeenCalled()
})
