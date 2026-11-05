import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../src/App'

vi.mock('../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench', () => ({
  default: ({ conversationId }) => <div>工作台 {conversationId}</div>,
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('独立分包入口', () => {
  it('登录后显示本人历史任务并可切换', async () => {
    let loggedIn = false
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (url === '/api/session') return { ok: loggedIn, status: loggedIn ? 200 : 401, json: async () => ({ username: 'alice' }) }
      if (url === '/api/login') { loggedIn = true; return { ok: true, json: async () => ({ username: 'alice' }) } }
      if (url === '/api/conversations' && options.method === 'POST') return { ok: true, json: async () => ({ id: 3 }) }
      if (url === '/api/conversations') return { ok: true, json: async () => [{ id: 2, title: '历史任务' }] }
      throw new Error('unexpected request')
    }))
    render(<App />)
    fireEvent.change(await screen.findByLabelText('账号'), { target: { value: 'alice' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'synthetic' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    fireEvent.click(await screen.findByRole('button', { name: '历史任务' }))
    expect(await screen.findByText('工作台 2')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '新建审查' }))
    await waitFor(() => expect(screen.getByText('工作台 3')).toBeTruthy())
    expect(screen.queryByText('注册')).toBeNull()
  })
})
