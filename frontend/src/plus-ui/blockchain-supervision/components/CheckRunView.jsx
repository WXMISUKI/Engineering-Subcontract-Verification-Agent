import { useEffect, useRef, useState } from 'react'
import { buildFlowState } from '../lib/flowModel'
import { buildFiveSteps } from '../lib/fiveStepModel'
import { bsEtaLabel, updateBsEta } from '../lib/bsEtaPlan'
import { formatEventTime } from '../lib/eventTime'
import { getBsBackgroundAiReview } from '../lib/api'

function stepStatus(step) {
  if (step.state === 'out_of_scope') return '本次不适用'
  if (step.state === 'failed' || step.state === 'error') return '核查失败'
  if (step.state === 'running') return '核查中…'
  if (step.state === 'not_started' || !step.state) return '等待中'
  if (step.issueCount > 0) return `发现 ${step.issueCount} 项`
  return '未发现问题'
}

function preparationProgress(materials, zipPreparation) {
  const activeMaterials = (materials || []).filter((material) => material?.superseded_by_id == null)
  const direct = activeMaterials.filter((material) => (
    material?.container_material_id == null && !/\.zip$/i.test(material?.file_name || '')
  ))
  const children = activeMaterials.filter((material) => material?.container_material_id != null)
  const rows = [...direct, ...children]
  const total = rows.length || (zipPreparation?.containers || []).reduce((sum, item) => sum + Number(item.child_count || 0), 0)
  const done = rows.filter((material) => ['done', 'failed'].includes(material?.parse_state)).length
    || (zipPreparation?.containers || []).reduce((sum, item) => sum + Number(item.children_done || 0) + Number(item.children_failed || 0), 0)
  const failed = rows.filter((material) => material?.parse_state === 'failed').length
    || (zipPreparation?.containers || []).reduce((sum, item) => sum + Number(item.children_failed || 0), 0)
  const current = rows.find((material) => ['uploaded', 'parsing', 'stored'].includes(material?.parse_state))
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return { total, done, failed, current, percent }
}

function eventText(event) {
  if (!event) return '系统事件'
  if (event.event_type === 'check.ai_advisory_completed' && event.label === 'AI 辅助核查已完成') return 'AI 核查结果已更新'
  return event.label || event.event_type || event.type || '系统事件'
}

const FAILURE_LABELS = {
  timeout: '模型调用超时', invalid_response: '模型返回格式无效', input_budget: '输入超过处理范围',
  model_call_error: '模型调用异常', rate_limit: '模型请求受限', authentication: '模型服务认证或额度异常',
  service_error: '模型服务暂时不可用', incomplete_material_scope: '材料范围不完整',
}

function timelineEvents(events) {
  const result = []
  const positions = new Map()
  const detailedRequests = new Set(events.filter((event) => event.event_type === 'check.ai_batch_progress' && event.progress_key).map((event) => event.progress_key))
  for (const event of events) {
    if (['check.ai_batch_checkpoint', 'check.negative_candidate_checkpoint'].includes(event.event_type) && detailedRequests.has(event.progress_key)) continue
    const groupable = event.progress_key && ['check.ai_advisory_started', 'check.ai_advisory_completed', 'check.ai_advisory_failed', 'check.negative_candidates_started', 'check.negative_candidates_finished', 'check.negative_candidates_failed'].includes(event.event_type)
    if (!groupable) {
      result.push(event)
      continue
    }
    // 同一请求对多个核查项续期或写结果，不应伪装成多次独立执行；不同请求与不同失败状态不合并。
    const key = JSON.stringify([event.event_type, event.progress_key, event.label, event.message])
    if (!positions.has(key)) {
      positions.set(key, result.length)
      result.push(event)
    } else if (event.event_type === 'check.ai_advisory_completed') {
      result[positions.get(key)] = event
    }
  }
  return result.sort((a, b) => (b.sequence ?? 0) - (a.sequence ?? 0))
}

/** 原型阶段 1：独立智能核查进度页。只展示服务端快照和真实 finding 投影。 */
export default function CheckRunView({
  taskId,
  taskState,
  snapshot,
  findings = [],
  autoCheck,
  activity,
  progress = null,
  events = [],
  materials = [],
  zipPreparation = null,
  supplementPlan = null,
  onRetry,
  onRecheck,
  onEnterReview,
  onReviewReady,
  publicationPending = false,
  mutationBusy = false,
}) {
  const flow = buildFlowState({ taskState, snapshot, findings })
  const visibleTimeline = timelineEvents(events)
  const fiveStep = buildFiveSteps({ snapshot, findings })
  const selectedSteps = fiveStep.steps.filter((step) => step.state !== 'out_of_scope')
  const total = selectedSteps.length
  const completedCount = flow.progress.completedSteps
  const completedFromTask = taskState === 'done'
  const coverageUnavailable = progress?.ai_coverage?.state === 'unavailable'
  const aiIncomplete = coverageUnavailable || (autoCheck?.stage === 'ai' && ['running', 'waiting', 'failed'].includes(autoCheck?.phase)) || progress?.phase === 'ai_checking'
  const serverFailed = progress?.phase === 'failed'
  const aiWorking = !coverageUnavailable && !serverFailed && autoCheck?.phase !== 'failed' && ((autoCheck?.stage === 'ai' && ['running', 'waiting'].includes(autoCheck?.phase)) || progress?.phase === 'ai_checking')
  const progressUnresolved = snapshot?.ai_auto_enabled === true && !completedFromTask && progress?.phase !== 'completed'
  const completed = Boolean(taskState) && !progressUnresolved && !publicationPending && !aiIncomplete && !serverFailed && (completedFromTask || (total > 0 && completedCount === total))
  const completionRunId = snapshot?.review_run_id
  const needsCompletionReceipt = completed && snapshot?.ai_auto_enabled === true && progress?.phase === 'completed'
    && !['running', 'waiting', 'resetting'].includes(autoCheck?.phase)
  const completionKey = `${taskId ?? ''}:${completionRunId ?? ''}`
  const [completionReceipt, setCompletionReceipt] = useState(null)
  const receiptState = !needsCompletionReceipt ? null : !taskId || !completionRunId ? 'unknown'
    : completionReceipt?.key === completionKey ? completionReceipt.state : 'loading'
  useEffect(() => {
    if (!needsCompletionReceipt || !taskId || !completionRunId) return undefined
    let active = true
    setCompletionReceipt({ key: completionKey, state: 'loading' })
    getBsBackgroundAiReview(taskId, completionRunId).then((job) => {
      if (!active) return
      const valid = job?.state === 'completed' && job.review_run_id === completionRunId
        && typeof job.job_id === 'string' && job.job_id && typeof job.warning === 'boolean'
      setCompletionReceipt({ key: completionKey, state: valid ? (job.warning ? 'warning' : 'clear') : 'unknown' })
    }).catch(() => {
      if (active) setCompletionReceipt({ key: completionKey, state: 'unknown' })
    })
    return () => { active = false }
  }, [needsCompletionReceipt, taskId, completionRunId, completionKey])
  useEffect(() => {
    if (completed) onReviewReady?.()
  }, [completed, onReviewReady])
  const preparing = taskState === 'preparing' || taskState === 'setup'
  const pageProgress = progress?.current_task
  const etaRef = useRef(null)
  const [etaNow, setEtaNow] = useState(() => Date.now())
  useEffect(() => {
    if (!preparing || !pageProgress?.material_id) {
      etaRef.current = null
      return
    }
    etaRef.current = updateBsEta(etaRef.current, pageProgress)
    setEtaNow(Date.now())
  }, [preparing, pageProgress?.material_id, pageProgress?.run_id, pageProgress?.source,
    pageProgress?.done_pages, pageProgress?.total_work_pages])
  useEffect(() => {
    if (!preparing) return undefined
    const timer = setInterval(() => setEtaNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [preparing])
  const localPreparation = preparationProgress(materials, zipPreparation)
  const readingStage = progress?.stages?.find((stage) => stage.key === 'material_reading')
  const preparation = readingStage ? {
    ...localPreparation,
    done: readingStage.completed_units ?? 0,
    total: readingStage.total_units,
  } : localPreparation
  const preparationTotalLabel = preparation.total == null ? '待确认' : preparation.total
  const failed = serverFailed || autoCheck?.phase === 'failed'
  const progressPercent = aiIncomplete || serverFailed ? null : Number.isFinite(progress?.percent) ? progress.percent : completed ? 100 : null
  const progressMode = aiIncomplete || serverFailed ? 'indeterminate' : progress?.mode || (progressPercent == null ? 'indeterminate' : 'determinate')
  const progressLabel = coverageUnavailable ? '无法核实' : failed ? '已中断' : progressPercent == null ? '处理中' : needsCompletionReceipt ? `规则 ${progressPercent}%` : `${progressPercent}%`
  const running = ['running', 'resetting', 'waiting'].includes(autoCheck?.phase)
  const progressMoving = !coverageUnavailable && !failed && (running || (progressMode === 'indeterminate'
    && !completed
    && !['done', 'failed'].includes(autoCheck?.phase)))
  const aiJob = progress?.ai_job || autoCheck?.job || null
  const staleCandidate = progress?.activity_health?.stale_candidate === true || aiJob?.stale_candidate === true
  const showStaleNotice = staleCandidate && !coverageUnavailable && !completed && !failed
  // 只有后端标记 running 的步骤才显示具体步骤，不根据完成数猜测当前步骤。
  const runningStep = selectedSteps.find((step) => step.state === 'running')
  const currentNo = runningStep ? selectedSteps.indexOf(runningStep) + 1 : null
  const runTitle = coverageUnavailable ? '核查状态待核实' : serverFailed ? '部分核查未完成' : aiWorking ? '正在生成 AI 辅助建议' : aiIncomplete ? 'AI 辅助核查未完成' : preparing
    ? '正在准备材料'
    : completed
      ? (needsCompletionReceipt ? '五步规则核查已结束' : '五步核查已全部完成')
    : total === 0
      ? '正在准备核查'
      : currentNo ? `正在执行第 ${currentNo} 步 / 共 ${total} 步` : '正在执行五步智能核查'
  const localCurrentTask = preparing
    ? (preparation.current?.file_name
      ? `正在读取材料：${preparation.current.file_name}`
      : preparation.total > preparation.done
        ? `正在读取材料（${preparation.done} / ${preparation.total}）`
        : '材料已读取，正在等待核查任务接管')
    : completed
        ? '核查结果已完成，等待人工复核'
        : total > 0
          ? (activity && activity !== '正在核查材料'
            ? activity
            : runningStep ? `正在执行第 ${currentNo} 步：${runningStep.title || '五步智能核查'}` : '正在执行五步智能核查')
          : '正在准备五步智能核查'
  const fallbackStages = [
    { key: 'read', label: '材料读取', detail: '正文、OCR、页码和材料归属', done: !preparing && ['running', 'review', 'done'].includes(taskState), current: preparing },
    { key: 'check', label: '五步智能核查', detail: '按五步顺序比对材料与规则', done: completed, current: !preparing && !completed },
  ]
  const currentAiGroup = aiJob?.current_group || autoCheck?.groupId || null
  const groupSteps = selectedSteps.filter((step) => step.expectedGroups.includes(currentAiGroup)).map((step) => step.title).filter(Boolean)
  const currentTask = coverageUnavailable ? 'AI 覆盖结果待核实' : failed ? '核查已中断，请根据提示处理' : aiWorking ? (groupSteps.length ? `${autoCheck?.activity === 'status' ? '正在确认核查状态' : '正在核查'}：${groupSteps.join('、')}` : progress?.current_task?.label || '正在处理已关联依据和材料') : receiptState === 'loading' ? '正在确认 AI 辅助核查状态' : receiptState === 'warning' || receiptState === 'unknown' ? '规则核查已结束，AI 辅助结果待核实' : progress?.current_task?.label || localCurrentTask
  const progressStages = Array.isArray(progress?.stages) && progress.stages.length > 0
    ? progress.stages.map((stage) => ({
      key: stage.key,
      label: stage.label,
      detail: stage.total_units == null
        ? (progress.unknown_reason || '总量确认中')
        : `${stage.key === 'intelligent_check' && (aiWorking || failed || aiIncomplete) ? '规则步骤 ' : ''}${stage.completed_units || 0} / ${stage.total_units}`,
      done: stage.state === 'completed',
      current: stage.state === 'running',
    }))
    : fallbackStages
  return (
    <section data-testid="check-run-view" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
      <div>
        <h1 className="text-2xl font-bold tracking-[.01em] text-[#1A1A1A]">{coverageUnavailable ? '核查状态待核实' : failed ? '核查已中断' : preparing ? '正在解析材料' : completed ? (needsCompletionReceipt ? '核查流程已结束' : '核查完成') : '正在核查'}</h1>
        <p className="mt-1 text-[15px] leading-relaxed text-[#6B7280]">系统按五步顺序比对材料与规则，只输出需要人工查看的问题。</p>
      </div>

      {supplementPlan?.fallback_message && <p role="status" className="rounded-lg border border-[#F0DCB8] bg-[#FFFDF8] px-4 py-3 text-sm text-[#92400E]">{supplementPlan.fallback_message}</p>}
      {autoCheck?.warning && <p role="status" className="rounded-lg border border-[#F0DCB8] bg-[#FFFDF8] px-4 py-3 text-sm text-[#92400E]">{autoCheck.warning}</p>}
      {receiptState === 'warning' && <p role="status" className="rounded-lg border border-[#F0DCB8] bg-[#FFFDF8] px-4 py-3 text-sm text-[#92400E]">部分 AI 辅助核查未完成或结果需要人工核实，请查看时间线并进入人工复核。</p>}
      {receiptState === 'unknown' && <p role="status" className="rounded-lg border border-[#F0DCB8] bg-[#FFFDF8] px-4 py-3 text-sm text-[#92400E]">AI 辅助核查结果状态无法核实，请查看时间线或联系人工复核。</p>}

      <div className="rounded-[10px] border border-[#E9E3D9] bg-white p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <strong data-testid="check-run-title" className="text-[16px] text-[#1A1A1A]">{runTitle}</strong>
            <p data-testid="check-run-note" className="mt-0.5 text-[15px] text-[#6B7280]">
              {coverageUnavailable ? '已有结果保留，需核实当前运行状态。' : failed ? '核查已中断，处理后可继续。' : preparing ? `已读取 ${preparation.done} / ${preparationTotalLabel} 份材料` : completed ? `共发现 ${flow.issueCount} 项需人工复核的问题。` : '整册解析完成，按五步比对规则中…'}
            </p>
          </div>
            <span data-testid="check-run-percent" className="shrink-0 text-[26px] font-bold leading-tight tabular-nums text-[#8B7355]">
            <span data-testid="progress-percent">{progressLabel}</span>
          </span>
        </div>

        <div className="my-3.5 h-1.5 overflow-hidden rounded-[3px] bg-[#EDE8DF]" aria-label={coverageUnavailable ? '核查状态无法核实' : failed ? '核查已中断' : progressPercent == null ? '当前阶段处理中' : `核查步骤进度 ${progressPercent}%`}>
          <div className={`relative h-full rounded-[3px] bg-[#8B7355] transition-[width] duration-300 ${progressMoving ? 'bs-progress-stripes' : ''}`} style={progressPercent == null ? { width: '100%' } : { width: `${progressPercent}%` }}>
            {progressMoving && <span data-testid="progress-motion" className="absolute inset-0" />}
          </div>
        </div>
        <div data-testid="check-run-current-task" className="rounded-[8px] border border-[#DCE5EE] bg-[#F7F9FB] px-3.5 py-2.5">
          <div className="flex items-center justify-between gap-3 text-[12px] text-[#8291A1]"><span>当前任务</span><span>{progressLabel}</span></div>
          <strong className="mt-0.5 block text-[14px] text-[#17283D]">{currentTask}</strong>
          {preparing && Number.isInteger(progress?.current_task?.current_page) && progress.current_task.current_page > 0 && (
            <p data-testid="check-run-page-progress" className="mt-1 text-[13px] text-[#587089]">
              {progress.current_task.source === 'text' ? '文本层扫描' : ['failed', 'retry_failed'].includes(progress.current_task.source) ? '读取失败' : '最近完成'}第 {progress.current_task.current_page}{Number.isInteger(progress.current_task.total_pages) ? ` / ${progress.current_task.total_pages}` : ''} 页
            </p>
          )}
          {preparing && Number.isInteger(pageProgress?.done_pages) && Number.isInteger(pageProgress?.total_work_pages) && pageProgress.total_work_pages > 0 && (
            <div className="mt-2">
              <div className="text-[12px] text-[#587089]">本阶段已处理 {pageProgress.done_pages} / {pageProgress.total_work_pages} 页</div>
              <div role="progressbar" aria-label="本阶段材料页处理进度" aria-valuemin={0}
                aria-valuemax={pageProgress.total_work_pages} aria-valuenow={Math.min(pageProgress.done_pages, pageProgress.total_work_pages)}
                className="mt-1 h-1.5 overflow-hidden rounded-full bg-[#DCE5EE]">
                <div className="h-full rounded-full bg-[#8B7355] transition-[width]" style={{ width: `${Math.min(100, Math.max(0, pageProgress.done_pages / pageProgress.total_work_pages * 100))}%` }} />
              </div>
            </div>
          )}
          {preparing && pageProgress?.material_id != null && (
            <p data-testid="check-run-page-eta" className="mt-1 text-[13px] text-[#587089]">本阶段读取：{bsEtaLabel(etaRef.current, etaNow)}</p>
          )}
        </div>
        {coverageUnavailable && (
          <div role="alert" className="mt-3 rounded-[8px] border border-[#F0D7A5] bg-[#FFF9EC] px-3.5 py-2.5 text-[13px] leading-relaxed text-[#8A5E1C]">
            {progress.ai_coverage.reason === 'rule_version_mismatch'
              ? '当前规则或核查实现与本次运行不一致，AI 核查状态无法核实。请联系人工复核，已有结果已保留。'
              : 'AI 核查状态无法核实，请联系人工复核；已有结果已保留。'}
          </div>
        )}
        {showStaleNotice && (
          <div data-testid="check-run-stale" role="alert" className="mt-3 rounded-[8px] border border-[#F0D7A5] bg-[#FFF9EC] px-3.5 py-2.5 text-[13px] leading-relaxed text-[#8A5E1C]">
            核查长时间没有新的进度回执，请刷新页面；若仍未恢复，再执行重试或联系人工复核。
          </div>
        )}
        {coverageUnavailable && aiJob?.last_recorded_state === 'completed' && <p className="mt-2 text-[13px] text-[#8A5E1C]">后台事件记录：已结束；结果状态无法核实</p>}
        <ol data-testid="check-run-progress-stages" className="mt-3 grid gap-2 md:grid-cols-3">
          {progressStages.map((stage, index) => (
            <li key={stage.key} className={`rounded-[8px] border px-3 py-2 ${stage.current ? 'border-[#D9C3A0] bg-[#FFFCF6]' : stage.done ? 'border-[#DCE5EE] bg-[#F7F9FB]' : 'border-[#E9E3D9] bg-white'}`}>
              <div className="flex items-center gap-2 text-[13px] text-[#17283D]"><span className={`grid h-5 w-5 place-items-center rounded-full border text-[11px] font-bold ${stage.done ? 'border-[#8B7355] bg-[#8B7355] text-white' : stage.current ? 'border-[#8B7355] text-[#8B7355]' : 'border-[#DCE5EE] text-[#8291A1]'}`}>{stage.done ? '✓' : index + 1}</span><strong>{stage.label}</strong></div>
              <p className="mt-1 pl-7 text-[11px] text-[#8291A1]">{stage.detail}</p>
            </li>
          ))}
        </ol>
        {failed && <div data-testid="check-run-error" role="alert" className="rounded-[8px] border border-[#F3C9C9] bg-[#FDF3F3] px-3 py-2 text-[13px] text-[#C62828]">自动核查未完成：{autoCheck?.error || (aiJob?.failure_categories || []).map((code) => FAILURE_LABELS[code]).filter(Boolean).join('、') || (serverFailed ? '请查看事件时间线并人工核实' : '服务暂不可用')}。{autoCheck?.recheckRequired ? '请点击“重新核查”；系统会保留已上传材料并留存当前结果，重新核查可能产生模型用量。' : '请根据需要重试或联系人工复核。'}</div>}
        {/* 完成态不再播报「当前活动」：事件流最后一条可能是与核查无关的事件标签
            （如「报告导出记录」），当进度播报会误导；原型完成态这里本来也是空的。 */}
        {!completed && !coverageUnavailable && (
          <p data-testid="check-run-activity" role="status" className="text-[14px] text-[#6B7280]">
            <span data-testid="current-activity">{failed ? (autoCheck?.recheckRequired ? '核查中断，请重新核查' : '核查未完成，请查看事件时间线') : activity || '正在准备核查结果…'}</span>
          </p>
        )}
        {autoCheck?.phase === 'done' && <p data-testid="auto-check-done" className="mt-1 text-[13px] text-[#2E7D32]">核查已完成，请进入人工复核</p>}

        {preparing && <div data-testid="check-run-preparation-status" className="mt-3 rounded-[8px] border border-[#DCE5EE] bg-[#F7F9FB] px-3.5 py-3">
          <div className="flex items-center justify-between gap-2 text-[13px] text-[#587089]"><strong className="text-[14px] text-[#17283D]">材料内容读取</strong><span>{preparation.done} / {preparationTotalLabel}</span></div>
          <p className="mt-1 text-[12px] text-[#587089]">{preparation.failed ? `有 ${preparation.failed} 份材料读取失败，相关核查项会保留为不可判定。` : preparation.current ? `正在读取：${preparation.current.file_name || '材料'}` : '后台准备任务已受理，等待读取结果。'}</p>
        </div>}

        <div data-testid="check-run-timeline" className="mt-3 rounded-[8px] border border-[#DCE5EE] bg-white px-3.5 py-3">
          <div className="mb-2 flex items-center justify-between"><strong className="text-[14px] text-[#17283D]">事件时间线</strong><span className="text-[12px] text-[#8291A1]">{visibleTimeline.length ? `共 ${visibleTimeline.length} 条` : '等待事件'}</span></div>
          {visibleTimeline.some((event) => event.event_type === 'check.ai_batch_progress') && <p className="mb-2 text-[12px] text-[#6B7280]">批次并发执行，回执按发生时间显示，批次编号可能不按顺序出现。</p>}
          {!visibleTimeline.length && (aiJob || progress?.activity_health?.last_event_at) && <div className="mb-2 break-words text-[12px] text-[#587089]">
            <p>最后回执：{aiJob?.last_progress_at || progress?.activity_health?.last_event_at ? formatEventTime(aiJob?.last_progress_at || progress.activity_health.last_event_at) : '无法核实'}{aiJob?.diagnostic_state ? ` · ${aiJob.diagnostic_state}` : ''}</p>
            {aiJob?.current_group && <span>分组 {aiJob.current_group} </span>}{aiJob?.current_batch?.index != null && <span>批次 {aiJob.current_batch.index}/{aiJob.current_batch.total ?? '?'} </span>}
            {aiJob?.job_id && <span>Job {aiJob.job_id} </span>}{aiJob?.request_id && <span>Request {aiJob.request_id}（不一定是失败请求）</span>}
          </div>}
          {visibleTimeline.length ? <ol className="max-h-[170px] space-y-1.5 overflow-y-auto text-[12px] text-[#587089]">{visibleTimeline.map((event) => <li key={event.sequence || `${event.created_at}-${eventText(event)}`} className="flex gap-2"><span className="shrink-0 tabular-nums text-[#8291A1]">{formatEventTime(event.created_at)}</span><span><b className="text-[#385A77]">{eventText(event)}</b>{event.message ? ` · ${event.message}` : ''}{(event.diagnostics?.failure_categories || []).map((code) => FAILURE_LABELS[code]).filter(Boolean).map((label) => <span key={label}> · {label}</span>)}{event.diagnostics && (event.diagnostics.job_id || event.diagnostics.request_id || event.diagnostics.batch_index != null) && <small className="ml-1 block break-words font-mono text-[10px] text-[#8291A1]">{event.diagnostics.job_id ? `job ${event.diagnostics.job_id}` : ''}{event.diagnostics.request_id ? ` · request ${event.diagnostics.request_id}` : ''}{event.diagnostics.batch_index != null ? ` · 批次 ${event.diagnostics.batch_index}/${event.diagnostics.batch_total ?? '?'}` : ''}</small>}</span></li>)}</ol> : <p className="text-[12px] text-[#8291A1]">后台事件到达后将在这里显示材料读取、核查和收口进度。</p>}
        </div>

        <ol data-testid="check-run-steps" className="mt-4 flex list-none flex-col gap-2 p-0">
          {fiveStep.steps.length === 0 && completedFromTask ? (
            <li data-testid="check-run-snapshot-unavailable" className="rounded-[8px] border border-dashed border-[#E9E3D9] bg-[#FCFBF8] px-3 py-2.5 text-[14px] leading-relaxed text-[#6B7280]">
              任务已完成，但本次没有返回五步冻结快照；已读取到 {findings.length} 条结论，可进入人工复核查看问题详情。
            </li>
          ) : fiveStep.steps.map((step) => {
            const aiGroups = Array.isArray(snapshot?.ai_auto_groups) ? snapshot.ai_auto_groups : null
            const aiMayAffectStep = !aiGroups || step.expectedGroups.some((group) => aiGroups.includes(group))
            const aiPendingForStep = !completed && !preparing && step.state === 'completed' && aiMayAffectStep
              && (snapshot?.ai_auto_enabled === true || aiIncomplete || serverFailed)
            const aiCurrentStep = aiPendingForStep && currentAiGroup && step.expectedGroups.includes(currentAiGroup)
            const state = aiCurrentStep && aiWorking ? 'running' : aiPendingForStep ? 'ai_pending' : step.state
            const done = state === 'completed'
            const failedStep = state === 'failed' || state === 'error'
            return (
              <li key={step.stepId || step.index} className={`flex items-center gap-3 rounded-[8px] border px-3 py-2.5 ${state === 'running' ? 'border-[#D9C3A0] bg-[#FFFCF6]' : done ? 'border-[#E9E3D9] bg-[#FDFCF9]' : 'border-[#E9E3D9] bg-white'}`}>
                <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[13px] font-bold ${done ? 'border-[#8B7355] bg-[#8B7355] text-white' : state === 'running' ? 'border-[#8B7355] bg-white text-[#8B7355]' : 'border-[#E9E3D9] bg-white text-[#6B7280]'}`}>
                  {done ? '✓' : step.index}
                </span>
                <div className="min-w-0 flex-1">
                  <strong className="block text-[15px] font-semibold text-[#1A1A1A]">{step.title}</strong>
                  <small className="block text-[14px] text-[#6B7280]">{step.description || step.question || '按本步范围核对材料与规则'}</small>
                </div>
                <span className={`shrink-0 text-[14px] ${failedStep || step.issueCount > 0 ? 'text-[#C62828]' : 'text-[#6B7280]'}`}>{aiPendingForStep ? (aiCurrentStep && aiWorking ? '规则已完成 · AI 辅助核查中' : '规则已完成 · AI 状态待核实') : needsCompletionReceipt && receiptState !== 'clear' && step.state === 'completed' && !step.issueCount ? '规则步骤已结束' : stepStatus(step)}</span>
              </li>
            )
          })}
        </ol>

        <div className="mt-4 flex min-h-9 items-center justify-end gap-3">
          {(autoCheck?.phase === 'failed' || (serverFailed && aiJob?.state === 'failed')) && <button type="button" disabled={mutationBusy || (autoCheck?.recheckRequired ? !onRecheck : !onRetry)} onClick={autoCheck?.recheckRequired ? onRecheck : onRetry} className="min-h-9 rounded-[6px] border border-[#C62828] px-3 text-[14px] text-[#C62828] disabled:cursor-not-allowed disabled:opacity-50">{autoCheck?.recheckRequired ? '重新核查' : '重试核查'}</button>}
          {showStaleNotice && <button type="button" disabled={mutationBusy || !onRetry} onClick={onRetry} className="min-h-9 rounded-[6px] border border-[#8A5E1C] px-3 text-[14px] text-[#8A5E1C] disabled:cursor-not-allowed disabled:opacity-50">重试核查</button>}
          {completed && autoCheck?.phase !== 'failed' && onRecheck && (
            <button type="button" disabled={mutationBusy} onClick={onRecheck} className="min-h-9 rounded-[6px] border border-[#8B7355] px-3 text-[14px] text-[#8B7355] disabled:cursor-not-allowed disabled:opacity-50">重新核查</button>
          )}
          {completed && (
            <button type="button" disabled={mutationBusy} onClick={onEnterReview} className="min-h-9 rounded-[6px] bg-[#8B7355] px-4 text-[14px] text-white disabled:cursor-not-allowed disabled:opacity-50">
              进入人工复核（{flow.issueCount}）
            </button>
          )}
        </div>
      </div>
    </section>
  )
}
