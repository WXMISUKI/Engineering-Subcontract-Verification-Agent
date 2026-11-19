import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileText, Play, RefreshCw, Trash2 } from 'lucide-react'
import { createBsTask, deleteBsMaterial, downloadBsReport, getBsRetrospective, getBsTask, saveBlob, startBsRetrospective, uploadBsMaterial } from './lib/api'

const COLUMNS = ['项目名称', '总包/分包', '合同文件', '合同条款位置', '合同相关摘录', '不符合或风险说明', '依据文件', '依据条款及原文摘录']
const button = 'inline-flex items-center gap-1.5 rounded border border-[#DADCE0] px-3 py-1.5 text-sm disabled:opacity-50'

export default function RetrospectiveWorkspace({ conversationId, taskId, onTaskCreated }) {
  const [task, setTask] = useState(null)
  const [status, setStatus] = useState(null)
  const [relations, setRelations] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const mutation = useRef(false)
  const refresh = useCallback(async (id) => {
    const [detail, result] = await Promise.all([getBsTask(id), getBsRetrospective(id)])
    setTask(detail); setStatus(result)
    return detail
  }, [])

  useEffect(() => {
    if (taskId == null) { setTask(null); setStatus(null); return undefined }
    let dead = false
    let timer
    const controller = new AbortController()
    const tick = async () => {
      try {
        const [detail, result] = await Promise.all([
          getBsTask(taskId, { signal: controller.signal }), getBsRetrospective(taskId, { signal: controller.signal }),
        ])
        if (dead) return
        setTask(detail); setStatus(result)
        if (['queued', 'running'].includes(result.state)) timer = setTimeout(tick, 2500)
      } catch (exc) { if (!dead) setError(exc.message) }
    }
    tick()
    return () => { dead = true; controller.abort(); clearTimeout(timer) }
  }, [taskId, retry])

  const upload = async (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length || mutation.current) return
    if (files.some((file) => !file.name.toLowerCase().endsWith('.pdf'))) { setError('请上传合同PDF'); return }
    mutation.current = true; setBusy(true); setError('')
    let id = taskId
    try {
      if (id == null) {
        const created = await createBsTask({ conversationId, title: '分包合同文本倒查', scopeA1: false, scopeA3: false,
          reviewMode: 'contract_retrospective' })
        id = created.task_id
        onTaskCreated(id)
      }
      for (const file of files) await uploadBsMaterial(id, file, { role: 'sub', materialCategory: 'sub' })
    } catch (exc) { setError(`${exc.message}。已上传的文件会保留，请查看清单后继续。`) }
    finally {
      if (id != null) {
        try { await refresh(id) } catch (exc) { setError(exc.message) }
      }
      mutation.current = false; setBusy(false)
    }
  }

  const materials = (task?.materials || []).filter((row) => !row.superseded_by_id && !row.replacement_target_id)
  const contracts = materials.filter((row) => ['sub', 'labor'].includes(row.role))
  const relationOf = (row) => contracts.length === 1 ? 'main' : (relations[row.material_id] || '')
  const ready = contracts.length > 0 && contracts.every((row) => relationOf(row))
    && contracts.filter((row) => relationOf(row) === 'main').length === 1
  const setup = taskId == null || (status?.state === 'not_started' && task?.state === 'setup')
  const report = status?.report

  const start = async () => {
    if (mutation.current || !ready) return
    mutation.current = true; setBusy(true); setError('')
    try {
      const result = await startBsRetrospective(taskId, contracts.map((row) => ({
        material_id: row.material_id, relation: relationOf(row),
      })))
      setStatus(result); setRetry((value) => value + 1)
    } catch (exc) { setError(exc.message); setRetry((value) => value + 1) }
    finally { mutation.current = false; setBusy(false) }
  }

  const remove = async (materialId) => {
    if (mutation.current) return
    mutation.current = true; setBusy(true); setError('')
    try { await deleteBsMaterial(taskId, materialId); await refresh(taskId) }
    catch (exc) { setError(exc.message) }
    finally { mutation.current = false; setBusy(false) }
  }

  const download = async (fmt) => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await downloadBsReport(taskId, fmt)
      saveBlob(blob, `分包合同文本倒查报告.${fmt}`)
    } catch (exc) { setError(exc.message) }
    finally { setBusy(false) }
  }

  return <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto bg-white text-[#202124]">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#DADCE0] px-6 py-3">
      <div><h1 className="text-base font-semibold">分包合同文本倒查</h1><p className="mt-0.5 text-xs text-[#5F6368]">合同核查完成后自动保存报告，可直接查看、对照和导出</p></div>
      {report && report.execution_status !== 'failed' && <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={() => download('docx')} className={button}><Download size={15}/> Word</button>
        <button type="button" disabled={busy} onClick={() => download('pdf')} className={button}><Download size={15}/> PDF</button>
      </div>}
    </header>
    {error && <div role="alert" className="mx-6 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}
      {taskId != null && <button type="button" onClick={() => { setError(''); setRetry((value) => value + 1) }} className="ml-3 underline">刷新状态</button>}
    </div>}
    {setup && <div className="mx-6 mt-5 max-w-4xl border-b border-[#DADCE0] pb-5">
      <h2 className="text-sm font-semibold">上传分包合同</h2>
      <p className="my-2 text-xs leading-relaxed text-[#5F6368]">仅需已审批的分包合同PDF；同一合同的附件、补充协议可一并上传。不要求资质、社保等准入资料。本版先核查单份分包合同，总包辅助比对将在下一阶段加入。</p>
      <label className={`${button} cursor-pointer`}><FileText size={16}/>选择合同PDF
        <input aria-label="上传分包合同PDF" type="file" accept="application/pdf,.pdf" multiple disabled={busy} onChange={upload} className="sr-only"/>
      </label>
      {contracts.length > 1 && <p className="mt-3 text-xs text-[#5F6368]">请指定一份合同正文，并为其余文件选择合同附件或补充协议。</p>}
      <ul className="mt-3 divide-y divide-[#E5E7EB]">{materials.map((row) => <li key={row.material_id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
        <span className="min-w-0 flex-1 break-all">{row.file_name}</span>
        {contracts.length > 1 && ['sub', 'labor'].includes(row.role) && <select aria-label={`${row.file_name}的合同关系`} value={relationOf(row)} disabled={busy}
          onChange={(event) => setRelations((value) => ({ ...value, [row.material_id]: event.target.value }))} className="rounded border border-[#DADCE0] px-2 py-1">
          <option value="">请选择归属</option><option value="main">合同正文</option><option value="attachment">合同附件</option><option value="supplement">补充协议</option>
        </select>}
        <button type="button" aria-label={`移除${row.file_name}`} disabled={busy} className={button} onClick={() => remove(row.material_id)}><Trash2 size={14}/>移除</button>
      </li>)}</ul>
      <button type="button" disabled={!ready || busy} onClick={start} className="mt-4 inline-flex items-center gap-2 rounded bg-[#176B5B] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"><Play size={15}/>{busy ? '处理中…' : '开始合同倒查'}</button>
    </div>}
    {taskId != null && !setup && !report && <div role="status" className="mx-6 mt-5 text-sm text-[#5F6368]">
      {status?.state === 'failed' ? (status.error || '倒查未完成，未生成风险结论') : <span className="inline-flex items-center gap-2"><RefreshCw size={15} className="animate-spin"/>合同正在读取或核查，完成后报告自动显示。可离开页面后返回查看。</span>}
      {status?.progress && <p className="mt-2 text-xs">正在检查第{status.progress.index}/{status.progress.total}批条款</p>}
    </div>}
    {report && <div className="min-w-0 flex-1 overflow-auto px-6 py-4">
      <div className="border-b border-[#DADCE0] pb-3"><h2 className="text-sm font-semibold">{report.summary}</h2><p className="mt-1 text-xs leading-relaxed text-[#5F6368]">{report.scope_note}</p>
        {report.coverage && <p className="mt-1 text-xs text-[#5F6368]">已完成{report.coverage.batches_completed}/{report.coverage.batches_total}批条款检查；位置采用PDF物理页码。</p>}
      </div>
      {report.limitations?.length > 0 && <details open className="my-3 text-xs text-[#6B5B00]"><summary>核查限制（{report.limitations.length}项）</summary><ul className="mt-1 list-disc space-y-1 pl-5">{report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}
      {report.execution_status !== 'failed' && <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[1450px] table-fixed border-collapse text-left text-xs">
        <colgroup>{[7, 5, 10, 10, 20, 22, 10, 16].map((width, i) => <col key={i} style={{ width: `${width}%` }}/>)}</colgroup>
        <thead><tr>{(report.columns || COLUMNS).map((column) => <th key={column} className="border border-[#DADCE0] bg-[#F8F9FA] px-2 py-2 font-semibold">{column}</th>)}</tr></thead>
        <tbody>{report.rows?.map((row) => <tr key={row.finding_id}>{row.cells.map((cell, index) => <td key={index} className="break-words whitespace-pre-wrap border border-[#DADCE0] px-2 py-2 align-top leading-relaxed">
          {index === 3 ? row.evidence.map((ref, j) => <div key={j}><a className="text-[#176B5B] underline" href={`/api/plus/blockchain-supervision/materials/${ref.material_id}/file#page=${ref.page}`} target="_blank" rel="noreferrer">{ref.file_name ? `${ref.file_name} · ` : ''}PDF第{ref.page}页{ref.clause ? ` · ${ref.clause}` : ''}</a></div>) : cell}
        </td>)}</tr>)}</tbody>
      </table>{!report.rows?.length && <p className="py-5 text-sm">当前已检查范围内未发现可列示问题。</p>}</div>}
    </div>}
  </section>
}
