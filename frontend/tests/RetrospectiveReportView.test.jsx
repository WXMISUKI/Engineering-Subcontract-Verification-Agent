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
    rows: [{ finding_id: 'f1', cells: ['项目', '总包/分包', '文件', '位置', '原文', '风险', '依据', '依据条款'], evidence: [
      { material_id: 6, file_name: '总包.pdf', page: 31, clause: '检测' },
      { material_id: 7, file_name: '分包.pdf', page: 36, clause: '9.2' },
    ] }], limitations: ['第24页未取得可靠正文'],
  }}/>)
  expect(screen.getByTestId('report-frame')).toBeTruthy()
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
