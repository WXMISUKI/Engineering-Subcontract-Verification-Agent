import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import PreviousSupplementReview from '../src/plus-ui/blockchain-supervision/components/PreviousSupplementReview'
import { getBsReviewVersion } from '../src/plus-ui/blockchain-supervision/lib/api'

vi.mock('../src/plus-ui/blockchain-supervision/lib/api', () => ({ getBsReviewVersion: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('补件中只读查看上次核查结果并明确未包含新材料', async () => {
  getBsReviewVersion.mockResolvedValue({ snapshot: { findings: [
    { finding_id: 7, item_id: 'RC-A07', status: 'undecidable', message: '企业负责人资料待核对' },
  ] } })
  render(<PreviousSupplementReview taskId={1} versionNo={2} />)
  expect(screen.getByText(/不含本次补件/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '查看上次核查结果' }))
  expect(await screen.findByText(/企业负责人资料待核对/)).toBeTruthy()
  expect(getBsReviewVersion).toHaveBeenCalledWith(1, 2)
})
