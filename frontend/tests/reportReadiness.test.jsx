import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReportView from '../src/plus-ui/blockchain-supervision/components/ReportView'
import { normalizeReportReadiness } from '../src/plus-ui/blockchain-supervision/lib/reportReadinessModel'

afterEach(cleanup)
export const ready = () => ({ schema_version: 'bs.report-readiness.v1', draft_readable: true,
  confirmed_output_ready: true, task_close_ready: true, execution: { state: 'completed' },
  reading_quality: { state: 'complete', pages: [] }, finding_review: { blocking: [] },
  blocking_reasons: [], next_actions: [] })

it.each([undefined, {}, { ...ready(), schema_version: 'v2' }, { ...ready(), confirmed_output_ready: 'true' },
  { ...ready(), execution: null }, { ...ready(), reading_quality: {} },
  { ...ready(), execution: { state: 'stale' } }])('blocks unknown or malformed readiness %j', (dto) => {
  const gate = normalizeReportReadiness(dto)
  expect(gate.confirmed_output_ready).toBe(false)
  expect(gate.task_close_ready).toBe(false)
})

it('revokes cached formal opinion buttons while preserving draft body', () => {
  const handler = vi.fn()
  const props = { prototypeMode: true, report: { sections: [], summary: {}, draft_notice: '草稿待核对' },
    opinion: { opinion_digest: 'cached' }, onGenerateOpinion: handler, onExportOpinion: handler, onExport: handler }
  const view = render(<ReportView {...props} reportReadiness={ready()} />)
  expect(screen.getByTestId('opinion-export-pdf').disabled).toBe(false)
  view.rerender(<ReportView {...props} reportReadiness={{ ...ready(), confirmed_output_ready: false,
    task_close_ready: false, execution: { state: 'incomplete' },
    blocking_reasons: [{ code: 'ai_execution_incomplete', message: 'AI 核查执行失败' }] }} />)
  for (const id of ['regenerate-formal-opinion', 'opinion-export-word', 'opinion-export-pdf', 'prototype-report-export-pdf']) {
    expect(screen.getByTestId(id).disabled).toBe(true)
    fireEvent.click(screen.getByTestId(id))
  }
  expect(handler).not.toHaveBeenCalled()
  expect(screen.getByTestId('report-readiness-disclosure').textContent).toContain('AI 核查执行失败')
})

it('allows report output with OCR warnings while retaining their disclosure', () => {
  const dto = { ...ready(), reading_quality: { state: 'pending', pages: [{ page: 2 }], blocking: false },
    warnings: [{ code: 'reading_quality_limited', message: '第 2 页识别不完整，此提示不阻断报告生成。' }] }
  expect(normalizeReportReadiness(dto).task_close_ready).toBe(true)
  const handler = vi.fn()
  render(<ReportView prototypeMode report={{ sections: [], summary: {} }} reportReadiness={dto}
    onComplete={handler} onExport={handler} />)
  expect(screen.getByTestId('prototype-report-export-word').disabled).toBe(false)
  expect(screen.getByTestId('report-close-task').disabled).toBe(false)
  expect(screen.queryByTestId('report-readiness-disclosure')).toBeNull()
  expect(screen.getByTestId('report-readiness-warnings').textContent).toContain('第 2 页识别不完整')
  fireEvent.click(screen.getByTestId('report-close-task'))
  expect(handler).toHaveBeenCalledOnce()
})

it('blocks missing task DTO even if the cached report claims ready', () => {
  render(<ReportView report={{ sections: [], summary: {}, report_readiness: ready() }} prototypeMode />)
  expect(screen.getByTestId('prototype-report-export-word').disabled).toBe(true)
})

it('uses refreshed task readiness after a cached output rejection', () => {
  render(<ReportView report={{ sections: [], summary: {} }} prototypeMode reportReadiness={ready()}
    blockingByStage={{ confirmed: { code: 'reading_incomplete', message: '过期阻断' } }} />)
  expect(screen.getByTestId('prototype-report-export-word').disabled).toBe(false)
})

it('allows candidate publication through closing while formal outputs stay blocked', () => {
  const onComplete = vi.fn()
  const view = render(<ReportView prototypeMode onComplete={onComplete} reportReadiness={{ ...ready(),
    draft_readable: false, confirmed_output_ready: false,
    blocking_reasons: [{ code: 'supplement_candidate_unpublished', message: '候选待发布' }] }} />)
  const button = screen.getByTestId('report-close-task')
  expect(button.disabled).toBe(false)
  fireEvent.click(button)
  expect(onComplete).toHaveBeenCalledOnce()
  view.rerender(<ReportView prototypeMode onComplete={onComplete} reportReadiness={{ ...ready(),
    confirmed_output_ready: false, task_close_ready: false }} />)
  expect(screen.getByTestId('report-close-task').disabled).toBe(true)
})
