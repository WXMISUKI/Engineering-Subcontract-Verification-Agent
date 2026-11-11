import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import EvidencePanel from '../src/plus-ui/blockchain-supervision/components/EvidencePanel'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('blocks an ignored review without a reason before calling the API', async () => {
  const onReview = vi.fn().mockResolvedValue(undefined)
  const finding = {
    id: 37,
    kind: 'completeness',
    status: 'undecidable',
    message: '请核对材料。',
    review: null,
  }
  render(<EvidencePanel findings={[finding]} materialsById={{}} onReview={onReview} />)

  fireEvent.click(screen.getByRole('button', { name: '处理' }))
  fireEvent.click(screen.getByRole('button', { name: '忽略' }))
  fireEvent.click(screen.getByRole('button', { name: '提交回执' }))

  expect(screen.getByTestId('review-row-note-error').textContent).toContain('忽略必须填写处理理由')
  expect(onReview).not.toHaveBeenCalled()

  fireEvent.change(screen.getByPlaceholderText('复核意见（选填）'), { target: { value: '原件已核对，暂不采纳' } })
  fireEvent.click(screen.getByRole('button', { name: '提交回执' }))
  expect(onReview).toHaveBeenCalledWith(finding, 'ignored', '原件已核对，暂不采纳')
})
