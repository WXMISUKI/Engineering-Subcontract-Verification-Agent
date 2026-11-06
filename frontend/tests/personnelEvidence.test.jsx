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
