import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'

vi.mock('../src/plus-ui/blockchain-supervision/components/MaterialPreviewModal', () => ({ default: (props) =>
  <div role="dialog" data-testid="source-preview">{props.activeMaterialId}:{props.page}<button onClick={props.onClose}>关闭</button></div> }))
afterEach(cleanup)

it('uses saved dual-side sources in shared report container and exports without a generation/review step', () => {
  const onExport = vi.fn()
  render(<ReportView reviewMode="contract_retrospective" onExport={onExport} report={{
    execution_status: 'partial', summary: '列示已核实的合同风险',
    materials: [{ material_id: 6, role: 'general', file_name: '总包.pdf' }, { material_id: 7, role: 'sub', file_name: '分包.pdf' }],
    template_document: { template_id: 'contract-retrospective.v6', blocks: [
      { type: 'heading', text: '一、核查概况' },
      { type: 'paragraph', text: '工程基本信息概述' },
      { type: 'heading', text: '二、重点问题' },
      { type: 'heading', text: '分包范围与主体资格' },
      { type: 'paragraph', text: '总包与分包约定的范围边界需进一步明确。' },
      { type: 'heading', text: '三、综合核查意见' },
      { type: 'paragraph', text: '本次已完成核查范围内列示1项风险。' },
      { type: 'heading', text: '附件一：合同问题与风险明细表' },
      { type: 'table', headers: ['项目名称', '总包/分包', '合同文件', '合同条款位置', '合同相关摘录', '不符合或风险说明', '依据文件', '依据条款及原文摘录'], rows: [] },
    ] },
    rows: [{ finding_id: 'f1', cells: ['项目', '总包/分包', '文件', '位置', '原文', '风险', '依据', '依据条款'], evidence: [
      { material_id: 6, file_name: '总包.pdf', page: 31, clause: '检测' },
      { material_id: 7, file_name: '分包.pdf', page: 36, clause: '9.2' },
    ] }], limitations: ['第24页未取得可靠正文'],
  }}/>)
  expect(screen.getByTestId('report-frame')).toBeTruthy()
  expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
    '一、核查概况', '二、重点问题', '分包范围与主体资格', '三、综合核查意见', '附件一：合同问题与风险明细表',
  ])
  const appendix = screen.getByRole('heading', { name: '附件一：合同问题与风险明细表' })
  const table = screen.getByRole('table')
  expect(appendix.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  const upstream = screen.getByRole('link', { name: /总包 · 总包.pdf · PDF第31页/ })
  expect(upstream.getAttribute('href')).toBe('/api/plus/blockchain-supervision/materials/6/file#page=31')
  fireEvent.click(upstream)
  expect(screen.getByTestId('source-preview').textContent).toContain('6:31')
  fireEvent.click(screen.getByText('关闭'))
  fireEvent.click(screen.getByRole('link', { name: /分包 · 分包.pdf · PDF第36页/ }))
  expect(screen.getByTestId('source-preview').textContent).toContain('7:36')
  fireEvent.click(screen.getByRole('button', { name: '导出 Word' }))
  expect(onExport).toHaveBeenCalledWith('draft', 'docx')
  expect(screen.queryByText(/人工复核|生成报告|未取得可靠正文/)).toBeNull()
})
