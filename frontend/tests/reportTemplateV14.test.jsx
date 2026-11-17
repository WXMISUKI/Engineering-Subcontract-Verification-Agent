import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const document = {
  template_id: 'v1.4',
  title: '测试施工专业分包审查意见',
  attachments: [{
    code: 'legality',
    title: '一、分包合法性审查',
    note: '判定：分包范围不得突破负面清单。',
    auto_headers: ['审查项', '核查结果', '核查说明', '补正要求', '责任单位'],
    auto_rows: [['分包范围合规性', '符合', '未发现异常', '—', '承包人']],
    manual_headers: [],
    manual_rows: [],
  }],
  blocks: [
    { type: 'paragraph', text: '以下依据《上海市公路工程施工分包管理实施细则》及报审材料，出具审查意见。' },
    { type: 'heading', text: '一、建设单位审查意见（正式表述）' },
    { type: 'paragraph', text: '工程名称：测试工程' },
    { type: 'paragraph', text: '分包单位：测试分包人' },
    { type: 'heading', text: '1. 资质与承接能力' },
    { type: 'paragraph', text: '结论：本节已核查范围内未发现不符合项。', bold: true },
    { type: 'paragraph', text: '· 已核查通过：营业执照、资质证书。', indent: true },
    { type: 'heading', text: '7. 结论' },
    { type: 'paragraph', text: '暂缓签订该分包合同，待下列事项补正后重新报审：', bold: true },
  ],
}
const report = { meta: {}, summary: {}, sections: [], business_document: { basic_information: {}, sections: [], conclusion: {} }, template_document: document }

it('renders the V1.4 formal opinion layout and action buttons', () => {
  render(<ReportView report={report} templateId="v1.4" prototypeMode onTemplateChange={vi.fn()} />)
  expect(screen.getByTestId('report-template-v14')).toBeTruthy()
  expect(screen.getByText('一、建设单位审查意见（正式表述）')).toBeTruthy()
  expect(screen.getByText('工程名称：测试工程')).toBeTruthy()
  expect(screen.getByText('1. 资质与承接能力')).toBeTruthy()
  expect(screen.getByText('7. 结论')).toBeTruthy()
  expect(screen.getByText('暂缓签订该分包合同，待下列事项补正后重新报审：')).toBeTruthy()
  expect(screen.queryByTestId('report-template-v13')).toBeNull()
  expect(screen.queryByTestId('generate-formal-opinion')).toBeNull()
  expect(screen.getByTestId('v14-generate-opinion')).toBeTruthy()
  expect(screen.getByTestId('v14-report-export-word')).toBeTruthy()
  expect(screen.getByTestId('v14-report-export-pdf')).toBeTruthy()
  expect(screen.getByTestId('v14-worksheet-export-word')).toBeTruthy()
  expect(screen.getByTestId('report-v14-worksheet')).toBeTruthy()
  expect(screen.getByText('一、分包合法性审查')).toBeTruthy()
  expect(screen.queryByTestId('prototype-report-exports')).toBeNull()
})

it('offers V1.4 in the template selector', () => {
  const change = vi.fn()
  render(<ReportView report={null} prototypeMode onTemplateChange={change} />)
  fireEvent.change(screen.getByLabelText('报告模板'), { target: { value: 'v1.4' } })
  expect(change).toHaveBeenCalledWith('v1.4')
})
