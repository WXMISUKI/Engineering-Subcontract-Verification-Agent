import { useCallback, useEffect, useRef, useState } from 'react'
import { PanelLeftClose, PanelLeftOpen, Trash2, X } from 'lucide-react'
import Workbench from './plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench'

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'include', ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : '操作失败，请稍后重试')
  }
  return response.status === 204 ? null : response.json()
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const user = await api('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) })
      setPassword('')
      onLogin(user)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <main className="flex min-h-full items-center justify-center bg-[#f7f1e9] p-6">
    <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-[#e7ddcf] bg-white p-8 shadow-sm">
      <h1 className="text-xl font-semibold text-[#302b25]">工程分包审查智能体</h1>
      <p className="mt-2 text-sm text-[#665f56]">请输入管理员提供的账号和密码。</p>
      <label className="mt-7 block text-sm font-medium" htmlFor="username">账号</label>
      <input id="username" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} className="mt-2 h-11 w-full rounded border border-[#c9c0b5] px-3" />
      <label className="mt-4 block text-sm font-medium" htmlFor="password">密码</label>
      <input id="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} className="mt-2 h-11 w-full rounded border border-[#c9c0b5] px-3" />
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <button disabled={busy} className="mt-6 h-11 w-full rounded bg-[#6b5a45] font-medium text-white disabled:opacity-60">{busy ? '登录中…' : '登录'}</button>
    </form>
  </main>
}

function DeleteConversationDialog({ row, busy, error, returnFocus, onCancel, onConfirm }) {
  const dialogRef = useRef(null)
  const cancelRef = useRef(null)
  const returnFocusRef = useRef(returnFocus)

  useEffect(() => {
    cancelRef.current?.focus()
    return () => { if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus() }
  }, [])

  function handleKeyDown(event) {
    if (event.key === 'Escape' && !busy) { event.preventDefault(); onCancel(); return }
    if (event.key !== 'Tab') return
    const buttons = Array.from(dialogRef.current.querySelectorAll('button:not(:disabled)'))
    if (!buttons.length) { event.preventDefault(); return }
    const first = buttons[0], last = buttons[buttons.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }

  return <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#201a14]/40 p-4" onKeyDown={handleKeyDown}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="delete-task-title" aria-describedby="delete-task-description" className="w-full max-w-[460px] rounded-2xl border border-[#e9e3d9] bg-white shadow-[0_24px_70px_rgba(35,28,20,0.22)]">
      <div className="flex items-start gap-4 px-6 pt-6">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#fdf0ed] text-[#b54b40]" aria-hidden="true"><Trash2 size={20} /></div>
        <div className="min-w-0 flex-1 pt-0.5">
          <h2 id="delete-task-title" className="text-[17px] font-semibold text-[#302b25]">删除审查任务？</h2>
          <p id="delete-task-description" className="mt-2 text-sm leading-6 text-[#665f56]">确定将这项任务从列表中移除吗？关联的材料和审查记录仍会保留。</p>
        </div>
        <button type="button" disabled={busy} aria-label="关闭" onClick={onCancel} className="-mr-2 -mt-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-[#8a8074] hover:bg-[#f6f3ef] hover:text-[#302b25] disabled:opacity-50"><X size={18} /></button>
      </div>
      <div className="mx-6 mt-5 rounded-lg border border-[#eee7de] bg-[#faf8f5] px-4 py-3 text-sm text-[#473f36]">
        <span className="block text-xs text-[#887d70]">待删除任务</span>
        <span className="mt-1 block break-words font-medium">{row.title}</span>
      </div>
      {error && <p role="alert" className="mx-6 mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      <div className="mt-6 flex justify-end gap-3 border-t border-[#f0ebe5] px-6 py-4">
        <button ref={cancelRef} type="button" disabled={busy} onClick={onCancel} className="min-h-11 rounded-lg border border-[#ddd5cb] px-5 text-sm font-medium text-[#51483e] hover:bg-[#f8f6f3] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#8b7355] disabled:opacity-50">取消</button>
        <button type="button" disabled={busy} onClick={onConfirm} className="min-h-11 rounded-lg bg-[#b54b40] px-5 text-sm font-medium text-white hover:bg-[#9d3e35] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#b54b40] disabled:opacity-60">{busy ? '删除中…' : '确认删除'}</button>
      </div>
    </section>
  </div>
}

export default function App() {
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(true)
  const [conversations, setConversations] = useState([])
  const [selected, setSelected] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleteError, setDeleteError] = useState('')
  const initialization = useRef(null)
  const deleteButtonRef = useRef(null)

  useEffect(() => { api('/api/session').then(setUser).catch(() => setUser(null)).finally(() => setChecking(false)) }, [])
  const refresh = useCallback(async () => {
    const rows = await api('/api/conversations')
    setConversations(rows)
    return rows
  }, [])
  const contentChanged = useCallback(hasContent => { if (hasContent) refresh().catch(() => {}) }, [refresh])
  useEffect(() => {
    if (!user) return
    let active = true
    if (!initialization.current) {
      initialization.current = (async () => {
        const rows = await api('/api/conversations')
        if (rows.length) return rows
        const created = await api('/api/conversations', { method: 'POST' })
        return [{ id: created.id, title: '新建审查' }]
      })()
    }
    initialization.current.then(rows => { if (active) { setConversations(rows); setSelected(rows[0].id) } })
      .catch(err => { if (active) { initialization.current = null; setError(err.message) } })
    return () => { active = false }
  }, [user])

  async function createConversation() {
    setBusy(true)
    setError('')
    try {
      const row = await api('/api/conversations', { method: 'POST' })
      setSelected(row.id)
      await refresh()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  async function deleteConversation() {
    const row = deleteTarget
    if (!row || busy) return
    setBusy(true)
    setDeleteError('')
    try {
      await api(`/api/conversations/${row.id}`, { method: 'DELETE' })
      const rows = conversations.filter(item => item.id !== row.id)
      setConversations(rows)
      setSelected(current => current === row.id ? (rows[0]?.id ?? null) : current)
      setDeleteTarget(null)
    } catch (err) { setDeleteError(err.message) } finally { setBusy(false) }
  }
  async function logout() {
    try { await api('/api/logout', { method: 'POST' }) } catch { /* 登录已过期时仍清除本地视图 */ }
    initialization.current = null
    setUser(null); setSelected(null); setConversations([])
  }
  if (checking) return <main className="p-6" role="status">正在检查登录状态…</main>
  if (!user) return <Login onLogin={setUser} />
  return <div className="flex h-full min-h-0 bg-[#fafaf9]">
    <aside className={`flex shrink-0 flex-col border-r border-[#e5ddd3] bg-[#f7f1e9] ${sidebarOpen ? 'w-64 p-4' : 'w-14 items-center py-4'}`} aria-label="审查任务">
      <div className={`flex items-center ${sidebarOpen ? 'justify-between' : 'justify-center'}`}>
        {sidebarOpen && <h1 className="font-semibold">工程分包审查</h1>}
        <button type="button" onClick={() => setSidebarOpen(value => !value)} aria-label={sidebarOpen ? '收起任务列表' : '展开任务列表'} aria-expanded={sidebarOpen} title={sidebarOpen ? '收起任务列表' : '展开任务列表'} className="flex h-10 w-10 items-center justify-center rounded text-[#665f56] hover:bg-white"><span aria-hidden="true">{sidebarOpen ? <PanelLeftClose size={19} /> : <PanelLeftOpen size={19} />}</span></button>
      </div>
      {sidebarOpen && <>
      <button onClick={createConversation} disabled={busy} className="mt-5 min-h-11 rounded border border-[#bda98f] bg-white px-3 text-left text-sm disabled:opacity-60">新建审查</button>
      <nav className="mt-4 flex-1 overflow-y-auto" aria-label="历史任务">
        {conversations.map(row => <div key={row.id} className={`mb-1 flex items-center rounded ${selected === row.id ? 'bg-white font-medium' : 'hover:bg-white/60'}`}>
          <button onClick={() => setSelected(row.id)} aria-current={selected === row.id ? 'page' : undefined} className="min-h-11 min-w-0 flex-1 truncate px-3 text-left text-sm">{row.title}</button>
          <button type="button" onClick={event => { deleteButtonRef.current = event.currentTarget; setDeleteError(''); setDeleteTarget(row) }} disabled={busy} aria-label={`删除${row.title}`} title="删除任务" className="flex h-10 w-10 shrink-0 items-center justify-center rounded text-[#776b5e] hover:bg-red-50 hover:text-red-700 disabled:opacity-50"><Trash2 size={16} aria-hidden="true" /></button>
        </div>)}
      </nav>
      <div className="mt-4 flex items-center justify-between gap-2 text-sm"><span className="truncate">{user.username}</span><button onClick={logout} className="min-h-11 px-2 underline">退出登录</button></div>
      </>}
    </aside>
    <main className="flex min-w-0 flex-1 flex-col">
      {error && <p role="alert" className="bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {selected ? <Workbench key={selected} conversationId={selected} onContentChange={contentChanged} /> :
        <div className="m-auto text-center"><p className="text-[#665f56]">选择历史任务或新建审查</p><button onClick={createConversation} disabled={busy} className="mt-4 min-h-11 rounded bg-[#6b5a45] px-5 text-white">新建审查</button></div>}
    </main>
    {deleteTarget && <DeleteConversationDialog row={deleteTarget} busy={busy} error={deleteError} returnFocus={deleteButtonRef.current} onCancel={() => setDeleteTarget(null)} onConfirm={deleteConversation} />}
  </div>
}
