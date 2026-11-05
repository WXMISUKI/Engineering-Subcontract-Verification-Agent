import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../src/App'

vi.mock('../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench', () => ({
  default: ({ conversationId }) => <div>工作台 {conversationId}</div>,
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('独立分包入口', () => {
  it('侧边栏收起后可从左侧重新展开', async () => {
    vi.stubGlobal('fetch', vi.fn(async url => {
      if (url === '/api/session') return { ok: true, json: async () => ({ username: 'alice' }) }
      if (url === '/api/conversations') return { ok: true, json: async () => [{ id: 1, title: '历史任务' }] }
      throw new Error('unexpected request')
    }))
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '收起任务列表' }))
    expect(screen.queryByRole('navigation', { name: '历史任务' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '展开任务列表' }))
    expect(screen.getByRole('navigation', { name: '历史任务' })).toBeTruthy()
  })
  it('确认后删除当前任务并切换到剩余任务，取消时不删除', async () => {
    let rows = [{ id: 2, title: '当前任务' }, { id: 1, title: '旧任务' }]
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/session') return { ok: true, json: async () => ({ username: 'alice' }) }
      if (url === '/api/conversations') return { ok: true, json: async () => rows }
      if (url === '/api/conversations/2' && options.method === 'DELETE') { rows = rows.filter(x => x.id !== 2); return { ok: true, status: 204 } }
      throw new Error(`unexpected request ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '删除当前任务' }))
    expect(screen.getByRole('dialog', { name: '删除审查任务？' })).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(fetchMock).not.toHaveBeenCalledWith('/api/conversations/2', expect.anything())
    fireEvent.click(screen.getByRole('button', { name: '删除当前任务' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(screen.getByText('工作台 1')).toBeTruthy())
    expect(screen.queryByRole('button', { name: '当前任务' })).toBeNull()
  })
  it('删除失败在弹窗中显示原因，Escape 可取消', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      if (url === '/api/session') return { ok: true, json: async () => ({ username: 'alice' }) }
      if (url === '/api/conversations') return { ok: true, json: async () => [{ id: 1, title: '处理中的任务' }] }
      if (options.method === 'DELETE') return { ok: false, json: async () => ({ detail: '任务正在处理，请等待完成后再删除' }) }
      throw new Error('unexpected request')
    }))
    render(<App />)
    const open = await screen.findByRole('button', { name: '删除处理中的任务' })
    fireEvent.click(open)
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', '任务正在处理，请等待完成后再删除')
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(open)
  })
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
