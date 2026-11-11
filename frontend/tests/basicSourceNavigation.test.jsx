import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
