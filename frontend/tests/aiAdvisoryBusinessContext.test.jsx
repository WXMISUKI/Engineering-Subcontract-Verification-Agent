import React from 'react'
import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import AiAdvisorySummary from '../src/plus-ui/blockchain-supervision/components/AiAdvisorySummary'

afterEach(cleanup)

it('在片段提示旁展示同一核查项的规则事实与原文', () => {
  render(<AiAdvisorySummary findings={[
    { finding_id: 5, item_id: 'A2-02', subject_id: 1, label: '证照有效期', message: '证照日期需核对原件' },
    { finding_id: 6, item_id: 'A2-02', subject_id: 1, label: '证照', message: '证照记载 2026-03-31' },
    { finding_id: 7, item_id: 'A2-02', subject_id: 2, label: '其他主体', message: '其他主体事实' },
    { finding_id: 8, item_id: 'A2-02', subject_id: 1, message: '已忽略事实', review: { decision: 'ignored' } },
  ]}
    items={[{ finding_id: 5, proposals: [{ requires_full_scope_review: true,
      message: '请结合完整报审材料核对该事项。', material_evidence: [{ page: 1, quote: '总监理工程师意见' }] }] }]} />)
  expect(screen.getByText(/证照日期需核对原件/)).toBeTruthy()
  expect(screen.getByText(/证照记载 2026-03-31/)).toBeTruthy()
  expect(screen.queryByText(/其他主体事实/)).toBeNull()
  expect(screen.queryByText(/已忽略事实/)).toBeNull()
  expect(screen.getByText(/材料第 1 页：总监理工程师意见/)).toBeTruthy()
})

it('建议较多时默认收起其余建议但保留原文', () => {
  const proposals = Array.from({ length: 5 }, (_, index) => ({ message: `提示${index + 1}` }))
  render(<AiAdvisorySummary findings={[{ finding_id: 5, label: '核查项' }]}
    items={[{ finding_id: 5, proposals }]} />)
  expect(screen.getByText('提示3').closest('details')).toBeNull()
  expect(screen.getByText('提示4').closest('details').open).toBe(false)
  expect(screen.getByText('查看其余 2 条辅助意见')).toBeTruthy()
})

it('显示选页后的范围限制并保留人工核实边界', () => {
  const limitation = 'MoMA 输入按已审阅的规则证据类别裁剪，其他授权页面未纳入；不能据此认定完整范围'
  render(<AiAdvisorySummary findings={[{ finding_id: 5, label: '转包范围' }]}
    items={[{ finding_id: 5, scope_selection: { narrowed: true }, limitations: [limitation], proposals: [] }]} />)
  expect(screen.getByText(limitation)).toBeTruthy()
})
