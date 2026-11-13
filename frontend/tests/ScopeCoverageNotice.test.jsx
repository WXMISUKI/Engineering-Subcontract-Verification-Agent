import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import ScopeCoverageNotice from '../src/plus-ui/blockchain-supervision/components/ScopeCoverageNotice'

afterEach(() => cleanup())

it('独立展示范围限制而不提供 finding 处置操作', () => {
  render(<ScopeCoverageNotice coverage={{
    state: 'partial',
    limitations: [
      { rule_id: 'T15-01', source_state: 'absent', subject_id: null },
      { rule_id: 'T15-03', source_state: 'absent', subject_id: null },
    ],
  }} />)

  const notice = screen.getByTestId('scope-coverage-notice')
  expect(within(notice).getByText('T15-01')).toBeTruthy()
  expect(within(notice).getByText('T15-03')).toBeTruthy()
  expect(within(notice).queryByRole('button')).toBeNull()
  expect(within(notice).getByText(
    '以下规则本轮未覆盖，未形成符合或不符合结论，也不会进入问题待处理清单。',
  )).toBeTruthy()
  expect(notice.textContent).not.toContain('finding')
})

it('覆盖核查未完成时显示状态，不把它显示成范围限制或空 finding', () => {
  render(<ScopeCoverageNotice coverage={{ state: 'incomplete', limitations: [] }} />)

  const notice = screen.getByTestId('scope-coverage-notice')
  expect(notice.textContent).toContain('范围覆盖核查尚未完成')
  expect(notice.textContent).not.toContain('本轮未覆盖')
})

it('旧代没有覆盖契约时不补造范围提示', () => {
  render(<ScopeCoverageNotice coverage={{ state: 'legacy_unknown', limitations: [] }} />)

  expect(screen.queryByTestId('scope-coverage-notice')).toBeNull()
})
