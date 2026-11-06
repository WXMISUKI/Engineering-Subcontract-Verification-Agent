import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import CheckRunView from '../src/plus-ui/blockchain-supervision/components/CheckRunView'

afterEach(cleanup)

it('核查时间线将历史事件和实时事件显示为北京时间', () => {
  render(<CheckRunView taskState="running" events={[
    { sequence: 1, event_type: 'task.started', label: '核查开始', created_at: '2026-11-06T01:00:15' },
    { sequence: 2, event_type: 'check.progress', label: '核查进行中', created_at: '2026-11-06T01:00:30Z' },
  ]} />)
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(timeline.getByText('09:00:15')).toBeTruthy()
  expect(timeline.getByText('09:00:30')).toBeTruthy()
})
