import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReviewWorkbenchLayout from '../src/plus-ui/blockchain-supervision/components/ReviewWorkbenchLayout'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('allows opening each page-only evidence reference', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
  const open = vi.fn()
  const finding = { id: 1, findingId: 1, message: '请核对人员证件', status: 'undecidable',
    material_id: 2, page: 16, quote: '任职通知', location_mode: 'page', evidence: [
      { material_id: 2, page: 19, quote: '身份证', location_mode: 'page', anchor: { status: 'precise', bbox: null } },
      { material_id: 2, page: 20, quote: '证书', location_mode: 'page', anchor: { status: 'precise', bbox: null } },
    ] }
  render(<ReviewWorkbenchLayout model={{ groups: [], selectedFinding: finding, selectedFindingId: 1,
    materialsById: { 2: { id: 2, file_name: '报审.pdf', parse_state: 'done' } } }} onOpenSource={open} />)
  fireEvent.click(screen.getByRole('button', { name: '定位原文 · 第 19 页' }))
  fireEvent.click(screen.getByRole('button', { name: '定位原文 · 第 20 页' }))
  expect(open).toHaveBeenCalledWith(2, 19, 1)
  expect(open).toHaveBeenCalledWith(2, 20, 1)
})

it('opens each supporting page in the review comparison view', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
  const open = vi.fn()
  const finding = { id: 1, findingId: 1, title: '人员证件差异', message: '请核对人员证件', status: 'undecidable',
    material_id: 2, materialId: 2, page: 16, quote: '任职通知', location_mode: 'page', locationMode: 'page', evidence: [
      { material_id: 2, page: 16, quote: '任职通知', location_mode: 'page' },
      { material_id: 2, page: 19, quote: '身份证', location_mode: 'page', anchor: { status: 'precise', bbox: null } },
      { material_id: 2, page: 20, quote: '证书', location_mode: 'page', anchor: { status: 'precise', bbox: null } },
    ] }
  render(<ReviewWorkbenchLayout prototypeMode model={{ groups: [{ key: 'group', items: [finding] }], selectedFinding: finding,
    selectedFindingId: 1, materialsById: { 2: { id: 2, file_name: '报审.pdf', parse_state: 'done' } } }} onOpenSource={open} />)
  fireEvent.click(screen.getByTestId('finding-card-1').querySelector('button'))
  fireEvent.click(screen.getByTestId('compare-open-evidence-0'))
  fireEvent.click(screen.getByTestId('compare-open-evidence-1'))
  expect(open).toHaveBeenCalledWith(2, 19, 1)
  expect(open).toHaveBeenCalledWith(2, 20, 1)
})

it('shows certificate authority roles and field reason', () => {
  const finding = {
    id: 1, findingId: 1, item_id: 'A2-05',
    message: '项目经理李强无法确认：补件证书编号未识别，不能回退原证书判通过',
    status: 'undecidable', reason_code: 'supplement_unreadable',
    reason_label: '补件证书不可读或缺号',
    material_id: 2, page: 1, quote: '交安 B', location_mode: 'page',
    evidence: [{
      material_id: 2, page: 1, quote: '交安 B', location_mode: 'page',
      certificate_evidence: [
        { material_id: 1, page: 2, role: 'superseded', certificate_number: '沪交安B25G00683' },
        { material_id: 2, page: 1, role: 'authoritative', generation_id: 'g-2' },
      ],
    }],
  }
  render(<ReviewWorkbenchLayout model={{
    groups: [], selectedFinding: finding, selectedFindingId: 1,
    materialsById: {
      1: { id: 1, file_name: '原报审材料.pdf', parse_state: 'done' },
      2: { id: 2, file_name: '补件证书.pdf', parse_state: 'done' },
    },
  }} onOpenSource={() => {}} />)
  expect(screen.getByTestId('detail-reason-code').textContent).toContain('补件证书不可读或缺号')
  expect(screen.getByTestId('certificate-evidence-item-0-0').textContent).toContain('已替代')
  expect(screen.getByTestId('certificate-evidence-item-0-0').textContent).toContain('原报审材料.pdf')
  expect(screen.getByTestId('certificate-evidence-item-0-1').textContent).toContain('权威补件')
  expect(screen.getByTestId('certificate-evidence-item-0-1').textContent).toContain('补件证书.pdf')
})

it('prototype compare view shows subject, reason code and generic evidence roles', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
  const finding = {
    id: 2, findingId: 2, item_id: 'A2-05', subjectLabel: '武汉龙辰建设有限公司',
    message: '证书编号与名单不一致', status: 'hit', reason_code: 'field_conflict',
    reason_label: '字段冲突', material_id: 2, page: 1, quote: '名单编号：A', location_mode: 'page',
    evidence: [
      { material_id: 2, page: 1, quote: '名单编号：A', role: 'expected', location_mode: 'page' },
      { material_id: 3, page: 2, quote: '证书编号：B', role: 'contradiction', location_mode: 'page' },
    ],
  }
  render(<ReviewWorkbenchLayout prototypeMode model={{
    groups: [{ key: 'group', items: [finding] }], selectedFinding: finding,
    selectedFindingId: 2,
    materialsById: {
      2: { id: 2, file_name: '名单.pdf', parse_state: 'done' },
      3: { id: 3, file_name: '证书.pdf', parse_state: 'done' },
    },
  }} onOpenSource={() => {}} />)
  fireEvent.click(screen.getByTestId('finding-card-2').querySelector('button'))
  expect(screen.getByTestId('compare-subject').textContent).toContain('武汉龙辰建设有限公司')
  expect(screen.getByTestId('compare-reason-code').textContent).toContain('字段冲突')
  expect(screen.getByText('名单/要求证据')).toBeTruthy()
  expect(screen.getByText('矛盾证据')).toBeTruthy()
})

it('explains publication generation and review policy in the workbench', () => {
  render(<ReviewWorkbenchLayout
    prototypeMode
    publication={{
      state: 'candidate',
      reviewPolicy: 'candidate_requires_review',
      generationId: 'baseline-3',
      candidateGenerationId: 'supplement-20261107',
      baselineVersionNo: 3,
    }}
    model={{ groups: [], selectedFinding: null, selectedFindingId: null, materialsById: {} }}
  />)
  const banner = screen.getByTestId('publication-policy-banner')
  expect(banner.textContent).toContain('候选代次')
  expect(banner.textContent).toContain('supplement-20261107')
  expect(banner.textContent).toContain('原人工意见不会自动继承')
})
