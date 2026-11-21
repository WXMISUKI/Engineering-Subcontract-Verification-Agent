import { useBsEventStream } from './hooks/useBsEventStream'
import { useCallback, useEffect, useRef, useState } from 'react'
import { associateBsGeneralSourceSet, createBsTask, deleteBsMaterial, downloadBsReport, getBsRetrospective, getBsTask,
  patchBsSection, removeBsGeneralSourceSetMember, resumeBsRetrospective, saveBlob, saveBsRetrospectiveContracts, setBsMaterialRole,
  startBsRetrospective, uploadBsMaterial } from './lib/api'
import PrototypeUploadPage from './prototype/PrototypeUploadPage'
import PrototypeV3Shell from './prototype/PrototypeV3Shell'
import CheckRunView from './components/CheckRunView'
import ReportView from './components/ReportView'
import { buildPrototypeTask } from './prototype/prototypeViewModel'
import { validateUploadFiles } from './lib/uploadFiles'

export default function RetrospectiveWorkspace({ conversationId, taskId, onTaskCreated, onReset, resetting = false }) {
  const [task, setTask] = useState(null)
  const [status, setStatus] = useState(null)
  const [uploadRole, setUploadRole] = useState('general')
  const [section, setSection] = useState({ baselineDate: '' })
  const sectionDirty = useRef(false)
  const inputRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [retry, setRetry] = useState(0)
  const [view, setView] = useState(null)
  const mutation = useRef(false)
  const scope = `${conversationId}:${taskId}`
  const activeScope = useRef(scope)
  activeScope.current = scope
  const refresh = useCallback(async (id) => {
    const [detail, result] = await Promise.all([getBsTask(id), getBsRetrospective(id)])
    if (activeScope.current === scope) { setTask(detail); setStatus(result) }
    return detail
  }, [scope])

  useEffect(() => {
    if (taskId == null) return undefined
    let dead = false
    let timer
    const controller = new AbortController()
    const tick = async () => {
      try {
        const [detail, result] = await Promise.all([
          getBsTask(taskId, { signal: controller.signal }), getBsRetrospective(taskId, { signal: controller.signal }),
        ])
        if (dead) return
        setTask(detail); setStatus(result); setReadError('')
        if (['queued', 'running'].includes(result.state) || detail.materials?.some((m) => ['parsing', 'uploading'].includes(m.parse_state))) timer = setTimeout(tick, 2500)
      } catch (exc) { if (!dead) { setReadError(exc.message); timer = setTimeout(tick, 5000) } }
    }
    tick()
    return () => { dead = true; controller.abort(); clearTimeout(timer) }
  }, [conversationId, taskId, retry])

  useEffect(() => {
    setTask(null); setStatus(null); setView(null); setError(''); setReadError('')
    setSection({ baselineDate: '' }); sectionDirty.current = false; setUploadRole('general')
  }, [conversationId, taskId])

  const setup = taskId == null || (status?.state === 'not_started' && task?.state === 'setup')
  const events = useBsEventStream(taskId, status?.run_id, { retrospective: true })
  const report = status?.report
  const stage = setup ? 'upload' : view || (report ? 'report' : 'run')
  const reached = setup ? 0 : report ? 2 : 1
  useEffect(() => { if (report) setView('report') }, [report?.identity?.run_id, Boolean(report)])
  const validation = task?.validation
  const relations = validation?.contract_relations || []
  const relationMap = Object.fromEntries(relations.map((row) => [row.material_id, row.relation]))
  const ready = validation?.hard_ok === true && !validation?.parsing_pending

  const upload = async (fileList, replacesMaterialId = null) => {
    const files = Array.from(fileList || [])
    if (!files.length || mutation.current) return
    const checked = validateUploadFiles(files)
    if (checked.emptyFiles.length || checked.oversizedFiles.length || checked.unsupportedFiles.length
        || files.some((file) => !file.name.toLowerCase().endsWith('.pdf'))) {
      setError('请上传非空的合同PDF，单文件不超过200MB'); return
    }
    mutation.current = true; setBusy(true); setError('')
    let id = taskId
    try {
      if (id == null) {
        const created = await createBsTask({ conversationId, title: '总分包合同文本倒查', scopeA1: false, scopeA3: false,
          reviewMode: 'contract_retrospective', ...section })
        id = created.task_id
        onTaskCreated(id)
      }
      const target = task?.materials?.find((m) => m.material_id === replacesMaterialId)
      const role = target?.role || uploadRole
      for (const file of files) await uploadBsMaterial(id, file, {
        role, materialCategory: role, replacesMaterialId,
      })
    } catch (exc) { setError(`${exc.message}。已上传的文件会保留，请查看清单后继续。`) }
    finally {
      if (id != null) {
        try { await refresh(id) } catch (exc) { setError(exc.message) }
      }
      mutation.current = false; setBusy(false); setRetry((value) => value + 1)
    }
  }

  const changeInput = async (action) => {
    if (mutation.current || !setup) return false
    mutation.current = true; setBusy(true); setError('')
    try { await action(); await refresh(taskId); return true }
    catch (exc) { setError(exc.message); return false }
    finally { mutation.current = false; setBusy(false) }
  }

  const start = async () => {
    if (mutation.current || !ready) return
    mutation.current = true; setBusy(true); setError('')
    try {
      // 开始前读取后端最新关系与门槛，不从本地文件数量推导就绪。
      if (sectionDirty.current) {
        await patchBsSection(taskId, section)
        sectionDirty.current = false
      }
      const detail = await refresh(taskId)
      if (!detail.validation?.hard_ok || detail.validation.parsing_pending) return
      const result = await startBsRetrospective(taskId, detail.validation.contract_relations)
      setView('run'); setStatus(result); setRetry((value) => value + 1)
    } catch (exc) { setError(exc.message); setRetry((value) => value + 1) }
    finally { mutation.current = false; setBusy(false) }
  }

  const resume = async () => {
    if (mutation.current || !status?.can_resume) return
    mutation.current = true; setBusy(true); setError('')
    try {
      const result = await resumeBsRetrospective(taskId)
      if (activeScope.current === scope) { setView('run'); setStatus(result) }
    } catch (exc) {
      if (activeScope.current === scope) setError(exc.message)
    } finally {
      mutation.current = false; setBusy(false)
      setRetry((value) => value + 1)
    }
  }

  const changeRelation = (materialId, relation) => changeInput(() => saveBsRetrospectiveContracts(taskId,
    [...relations.filter((row) => row.material_id !== materialId), ...(relation ? [{ material_id: materialId, relation }] : [])]))
  const changeSection = (key, value) => {
    setSection((current) => ({ ...current, [key]: value || null }))
    sectionDirty.current = true
    if (taskId != null && key === 'baselineDate') {
      return changeInput(() => patchBsSection(taskId, { [key]: value || null }))
    }
  }
  const commitSection = async () => {
    if (taskId == null || !sectionDirty.current) return
    if (await changeInput(() => patchBsSection(taskId, section))) sectionDirty.current = false
  }
  const uploadTask = buildPrototypeTask(task || { state: 'setup', materials: [] })
  if (!validation) uploadTask.validation = { hard_ok: false, hard_gaps: ['请上传分包合同'] }
  uploadTask.section = { ...uploadTask.section, ...section,
    baselineDate: section.baselineDate ?? (task?.baseline_date_source === 'manual' ? task?.baseline_date : '') }

  const download = async (fmt) => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await downloadBsReport(taskId, fmt)
      saveBlob(blob, `分包合同文本倒查报告.${fmt}`)
    } catch (exc) { setError(exc.message) }
    finally { setBusy(false) }
  }

  return <PrototypeV3Shell reviewMode="contract_retrospective" taskState={setup ? 'setup' : report ? 'done' : 'running'} stage={stage} reachedIndex={reached} onStageSelect={setView}><section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto bg-white text-[#202124]">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#DADCE0] px-6 py-3">
      <div><h1 className="text-base font-semibold">分包合同文本倒查</h1><p className="mt-0.5 text-xs text-[#5F6368]">合同核查完成后自动保存报告，可直接查看、对照和导出</p></div>
    </header>
    {(error || readError) && <div role="alert" className="mx-6 mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error || readError}
      {taskId != null && <button type="button" onClick={() => { setError(''); setReadError(''); setRetry((value) => value + 1) }} className="ml-3 underline">刷新状态</button>}
    </div>}
    {stage === 'upload' && <>
      <input ref={inputRef} aria-label="上传合同PDF" type="file" accept="application/pdf,.pdf" multiple hidden disabled={busy}
        onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ''; upload(files) }} />
      <PrototypeUploadPage task={uploadTask} reviewMode="contract_retrospective" uploadRole={uploadRole}
        readOnly={!setup} onExit={() => setView(report ? 'report' : 'run')}
        busy={busy} onReset={setup ? onReset : undefined} resetting={resetting} contractRelations={relationMap}
        onRoleChange={setUploadRole} onSectionChange={changeSection} onSectionBlur={commitSection} onContractRelationChange={changeRelation}
        onSelectFiles={() => inputRef.current?.click()} onDropFiles={upload}
        onMaterialRoleChange={(id, role) => changeInput(() => setBsMaterialRole(taskId, id, role))}
        onAssociateGeneralSourceSet={(ids) => changeInput(() => associateBsGeneralSourceSet(taskId, ids))}
        onRemoveGeneralSourceSetMember={(id) => changeInput(() => removeBsGeneralSourceSetMember(taskId, id))}
        onRemove={(id) => changeInput(() => deleteBsMaterial(taskId, id))}
        onReplace={(id, file) => upload([file], id)} onStart={start} />
    </>}
    {stage === 'run' && <div className="min-h-0 flex-1 overflow-auto p-6"><CheckRunView reviewMode="contract_retrospective" retrospective={status || {}} events={events} onRefresh={() => setRetry((value) => value + 1)} onResume={resume} resuming={busy} onOpenReport={() => setView('report')}/></div>}
    {stage === 'report' && report && <ReportView reviewMode="contract_retrospective" report={report} materials={task?.materials || []} exporting={busy ? 'busy' : null} onExport={(_, fmt) => download(fmt)} onBack={() => setView('run')}/>}
  </section></PrototypeV3Shell>
}
