// @vitest-environment jsdom
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ReviewModeSelect from '../src/plus-ui/blockchain-supervision/components/ReviewModeSelect'
import { createBsTask } from '../src/plus-ui/blockchain-supervision/lib/api'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('defaults to admission and allows selecting retrospective before creation', () => {
  const change = vi.fn()
  render(<ReviewModeSelect onChange={change} />)
  const selector = screen.getByRole('combobox', { name: '核查阶段' })
  expect(selector.value).toBe('admission')
  fireEvent.change(selector, { target: { value: 'contract_retrospective' } })
  expect(change).toHaveBeenCalledWith('contract_retrospective')
})

it('restores saved mode and locks the task selection', () => {
  render(<ReviewModeSelect value="contract_retrospective" locked onChange={vi.fn()} />)
  const selector = screen.getByRole('combobox', { name: '核查阶段' })
  expect(selector.value).toBe('contract_retrospective')
  expect(selector.disabled).toBe(true)
})

it('sends the task mode to task creation rather than global model settings', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ task_id: 7 }) })
  vi.stubGlobal('fetch', fetchMock)
  await createBsTask({ conversationId: 5, title: '合同倒查', reviewMode: 'contract_retrospective' })
  const [url, request] = fetchMock.mock.calls[0]
  expect(url).toContain('/blockchain-supervision/tasks')
  expect(JSON.parse(request.body).review_mode).toBe('contract_retrospective')
})
