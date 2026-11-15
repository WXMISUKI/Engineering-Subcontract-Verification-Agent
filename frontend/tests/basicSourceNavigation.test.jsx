import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReviewWorkbenchLayout from '../src/plus-ui/blockchain-supervision/components/ReviewWorkbenchLayout'

vi.mock('../src/plus-ui/blockchain-supervision/components/MaterialPreviewPane', () => ({
  default: ({ material, page }) => <div data-testid="preview-target">{material?.file_name} 第{page}页</div>,
}))
afterEach(cleanup)

it('opens a basic information source at its page without a finding', () => {
  render(<ReviewWorkbenchLayout prototypeMode overviewMode overviewPanel={<div>核查概览</div>}
    model={{ groups: [], materialsById: { 7: { id: 7, file_name: '报审表.pdf', parse_state: 'done' } } }}
    previewMaterialId={7} previewPage={2} previewClosed={false} />)
  expect(screen.getByTestId('preview-target').textContent).toBe('报审表.pdf 第2页')
})

it('opens a suspect source page directly from reading coverage', () => {
  const open = vi.fn()
  render(<ReviewWorkbenchLayout prototypeMode model={{ groups: [], materialsById: {
    7: { id: 7, file_name: '报审表.pdf', parse_state: 'done' },
  } }} readingCoverage={{ complete: false, materials: [{ material_id: 7, file_name: '报审表.pdf',
    reading_complete: false, unread_pages: [20, 21], suspect_pages: [21] }] }} onOpenSource={open} />)
  fireEvent.click(screen.getByRole('button', { name: '报审表.pdf 第 21 页（待读）' }))
  expect(open).toHaveBeenCalledWith(7, 21, null)
})

it('shows the report-generation busy state in the review queue', () => {
  render(<ReviewWorkbenchLayout prototypeMode model={{ groups: [] }} onComplete={vi.fn()} completing />)
  const button = screen.getByRole('button', { name: '正在生成报告…' })
  expect(button.disabled).toBe(true)
  expect(button.getAttribute('aria-busy')).toBe('true')
})
