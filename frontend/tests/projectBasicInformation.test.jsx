import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import ProjectBasicInformation from '../src/plus-ui/blockchain-supervision/components/ProjectBasicInformation'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'

afterEach(cleanup)

it('shows parsed facts and lets users open their source without confirmation', () => {
  const onOpenSource = vi.fn()
  render(<ProjectBasicInformation detail={{ report_basic_facts: { subcontract_amount: {
    status: 'matched', value: '4173.0485', unit: '万元', evidence: [{ material_id: 3, page: 1, file_name: '报审表.pdf' }],
  } } }} onOpenSource={onOpenSource} />)
  expect(screen.getByText('4173.0485万元')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '报审表.pdf 第1页' }))
  expect(onOpenSource).toHaveBeenCalledWith(3, 1)
})

it('offers a return to basic information for unresolved report fields', () => {
  const onShowBasicInformation = vi.fn()
  const report = { meta: {}, sections: [], summary: {}, business_document: {
    basic_information: { '拟分包范围': null }, sections: [], issues: [], conclusion: {},
  } }
  render(<ReportView report={report} prototypeMode onShowBasicInformation={onShowBasicInformation} />)
  fireEvent.click(screen.getByRole('button', { name: '查看项目基本信息' }))
  expect(onShowBasicInformation).toHaveBeenCalledOnce()
})

it('shows task and resolved contract facts before a report exists', () => {
  render(<ProjectBasicInformation detail={{ section: { project_name: '任务工程', contract_section: '11标' },
    subjects: [{ name: '分包公司', name_status: 'confirmed' }], contract_party_evidence: { general_contractor: { status: 'matched', company: '总包公司' } },
    report_basic_facts: {} }} />)
  for (const value of ['任务工程', '11标', '分包公司', '总包公司']) expect(screen.getByText(value)).toBeTruthy()
})

it('does not replace a reported conflict with a stale candidate', () => {
  const report = { basic_information: { general_contractor: { status: 'conflict', company: '候选公司' } },
    business_document: { basic_information: { '施工总承包单位': null }, basic_information_status: { '施工总承包单位': 'conflict' } } }
  render(<ProjectBasicInformation report={report} />)
  const region = screen.getByRole('region', { name: '项目基本信息' })
  expect(within(region).getByText('待核实（材料冲突）')).toBeTruthy()
  expect(within(region).queryByText(/候选公司/)).toBeNull()
})

it('shows a task value and a different material candidate for comparison', () => {
  const onOpenSource = vi.fn()
  render(<ProjectBasicInformation detail={{ section: { project_name: '任务工程' }, report_basic_facts: {
    project_name: { status: 'matched', value: '材料工程', evidence: [{ value: '材料工程', material_id: 7, page: 2, file_name: '报审表.pdf' }] },
  } }} onOpenSource={onOpenSource} />)
  expect(screen.getByText('任务工程（待核实）')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '报审表.pdf 第2页（材料工程）' }))
  expect(onOpenSource).toHaveBeenCalledWith(7, 2)
})

it('uses the backend equivalence status for a full and short project title', () => {
  render(<ProjectBasicInformation detail={{ section: { project_name: 'G15公路嘉金段改扩建工程主线施工11标' }, report_basic_facts: {
    project_name: { source: 'task', status: 'matched', value: 'G15公路嘉金段改扩建工程主线施工11标',
      material_candidates: [{ value: 'G15公路嘉金段（G1503公路-S32公路）改扩建工程主线施工11标' }] },
  } }} />)
  expect(screen.getByText('G15公路嘉金段改扩建工程主线施工11标')).toBeTruthy()
})

it('shows recipient as an unconfirmed candidate and allows an explicit confirmation', () => {
  const onConfirmEmployer = vi.fn()
  const onOpenSource = vi.fn()
  const candidate = { material_id: 4, page: 1, file_name: '报审表.pdf', value: '沪申公司', quote: '致：沪申公司' }
  render(<ProjectBasicInformation detail={{ contract_party_evidence: { employer: {
    status: 'unresolved', company: null, recipient_candidates: [candidate],
  } } }} onOpenSource={onOpenSource} onConfirmEmployer={onConfirmEmployer} />)
  expect(screen.getByText('收件人候选：沪申公司（未确认）')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '确认沪申公司为建设单位' }))
  expect(onConfirmEmployer).toHaveBeenCalledWith(candidate)
  fireEvent.click(screen.getByRole('button', { name: '查看收件人原文（第1页）' }))
  expect(onOpenSource).toHaveBeenCalledWith(4, 1)
})
