import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ReviewActions from '../src/plus-ui/blockchain-supervision/components/ReviewActions'

afterEach(() => { cleanup() })

const baseFinding = {
  id: 7,
  status: 'undecidable',
  message: '现场证据需要人工核实',
}

it('忽略必须填写理由，提交后携带 accepted/ignored 规范值', () => {
  const onReview = vi.fn()
  render(<ReviewActions finding={baseFinding} actions={{ canReview: true }} onReview={onReview} />)

  fireEvent.click(screen.getByRole('button', { name: '忽略' }))
  expect(screen.getByTestId('review-note-error').textContent).toContain('忽略必须填写处理理由')
  expect(onReview).not.toHaveBeenCalled()

  fireEvent.change(screen.getByLabelText('处理意见'), { target: { value: '已核对原件，证据仍不足' } })
  fireEvent.click(screen.getByRole('button', { name: '忽略' }))
  expect(onReview).toHaveBeenCalledWith(7, 'ignored', '已核对原件，证据仍不足')
})

it('结论变化后的失效回执回到待处置，并显示历史留痕提示', () => {
  render(<ReviewActions
    finding={{
      ...baseFinding,
      review: {
        invalidated: { reason: 'conclusion_changed', previous: { decision: 'accepted' } },
      },
    }}
    actions={{ canReview: true }}
  />)

  expect(screen.getByTestId('review-invalidated-note').textContent).toContain('原复核意见已失效')
  expect(screen.getByRole('button', { name: '采纳' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '忽略' })).toBeTruthy()
})
