import { formatEventTime } from '../lib/eventTime'

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


export function CheckRunTimeline({ events = [], failed = false, coverageUnavailable = false, aiJob, progress }) {
  const visibleTimeline = timelineEvents(events)
  return (
        <details data-testid="check-run-timeline" open={failed || coverageUnavailable} className="mt-3 rounded-[8px] border border-[#DCE5EE] bg-white px-3.5 py-3">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-[14px] text-[#17283D] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#174C7E]">
            <strong>事件时间线</strong><span className="text-[12px] text-[#8291A1]">{visibleTimeline.length ? `共 ${visibleTimeline.length} 条` : '等待事件'}</span>
          </summary>
          <div className="mt-2">
          {visibleTimeline.some((event) => event.event_type === 'check.ai_batch_progress') && <p className="mb-2 text-[12px] text-[#6B7280]">批次并发执行，回执按发生时间显示，批次编号可能不按顺序出现。</p>}
          {!visibleTimeline.length && (aiJob || progress?.activity_health?.last_event_at) && <div className="mb-2 break-words text-[12px] text-[#587089]">
            <p>最后回执：{aiJob?.last_progress_at || progress?.activity_health?.last_event_at ? formatEventTime(aiJob?.last_progress_at || progress.activity_health.last_event_at) : '无法核实'}{aiJob?.diagnostic_state ? ` · ${aiJob.diagnostic_state}` : ''}</p>
            {aiJob?.current_group && <span>分组 {aiJob.current_group} </span>}{aiJob?.current_batch?.index != null && <span>批次 {aiJob.current_batch.index}/{aiJob.current_batch.total ?? '?'} </span>}
            {aiJob?.job_id && <span>Job {aiJob.job_id} </span>}{aiJob?.request_id && <span>Request {aiJob.request_id}（不一定是失败请求）</span>}
          </div>}
          {visibleTimeline.length ? <ol className="max-h-[170px] space-y-1.5 overflow-y-auto text-[12px] text-[#587089]">{visibleTimeline.map((event) => <li key={event.sequence || `${event.created_at}-${eventText(event)}`} className="flex gap-2"><span className="shrink-0 tabular-nums text-[#8291A1]">{formatEventTime(event.created_at)}</span><span><b className="text-[#385A77]">{eventText(event)}</b>{event.message ? ` · ${event.message}` : ''}{(event.diagnostics?.failure_categories || []).map((code) => FAILURE_LABELS[code]).filter(Boolean).map((label) => <span key={label}> · {label}</span>)}{event.diagnostics && (event.diagnostics.job_id || event.diagnostics.request_id || event.diagnostics.batch_index != null) && <small className="ml-1 block break-words font-mono text-[10px] text-[#8291A1]">{event.diagnostics.job_id ? `job ${event.diagnostics.job_id}` : ''}{event.diagnostics.request_id ? ` · request ${event.diagnostics.request_id}` : ''}{event.diagnostics.batch_index != null ? ` · 批次 ${event.diagnostics.batch_index}/${event.diagnostics.batch_total ?? '?'}` : ''}</small>}</span></li>)}</ol> : <p className="text-[12px] text-[#8291A1]">后台事件到达后将在这里显示材料读取、核查和收口进度。</p>}
          </div>
        </details>
  )
}

export function CheckRunStages({ stages: progressStages }) {
  return (
        <ol data-testid="check-run-progress-stages" className="mt-3 grid gap-2 md:grid-cols-3">
          {progressStages.map((stage, index) => (
            <li key={stage.key} className={`rounded-[8px] border px-3 py-2 ${stage.current ? 'border-[#D9C3A0] bg-[#FFFCF6]' : stage.done ? 'border-[#DCE5EE] bg-[#F7F9FB]' : 'border-[#E9E3D9] bg-white'}`}>
              <div className="flex items-center gap-2 text-[13px] text-[#17283D]"><span className={`grid h-5 w-5 place-items-center rounded-full border text-[11px] font-bold ${stage.done ? 'border-[#8B7355] bg-[#8B7355] text-white' : stage.current ? 'border-[#8B7355] text-[#8B7355]' : 'border-[#DCE5EE] text-[#8291A1]'}`}>{stage.done ? '✓' : index + 1}</span><strong>{stage.label}</strong></div>
              <p className="mt-1 pl-7 text-[11px] text-[#8291A1]">{stage.detail}</p>
            </li>
          ))}
        </ol>
  )
}

export function CheckRunFrame({ title, description, children }) {
  return <section data-testid="check-run-view" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto"><div><h1 className="text-2xl font-bold tracking-[.01em] text-[#1A1A1A]">{title}</h1><p className="mt-1 text-[15px] leading-relaxed text-[#6B7280]">{description}</p></div>{children}</section>
}
