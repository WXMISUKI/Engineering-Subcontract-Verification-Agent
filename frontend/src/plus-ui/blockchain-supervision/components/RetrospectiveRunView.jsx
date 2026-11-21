import { CheckRunFrame, CheckRunStages, CheckRunTimeline } from './CheckRunPresentation'

export default function RetrospectiveRunView({ retrospective = {}, events = [], onRefresh, onResume, resuming, onOpenReport }) {
  const { state, progress, report, error, can_resume: canResume } = retrospective
  const saved = Boolean(report && report.execution_status !== 'failed')
  const failed = state === 'failed' || report?.execution_status === 'failed'
  const partial = saved && report.execution_status === 'partial' && !report.required_stages_completed
  const title = failed ? '合同核查未完成' : saved ? (partial ? '已保存部分核查结果' : '合同核查已完成') : '正在核查合同'
  const batch = Number.isInteger(progress?.index) && progress?.total > 1
    ? ` · ${progress.index}/${progress.total}批` : ''
  const active = progress?.label || '正在准备合同核查'
  // 分母是当前活动的批次数，不是整次运行的百分比；保存报告才算执行收尾。
  const percent = saved ? 100 : progress?.total > 1 ? Math.min(99, Math.max(0, progress.index / progress.total * 100)) : null
  return <CheckRunFrame title="合同智能核查" description="核对分包合同条款、负面清单及相关规范。有确认对应的总包时，再比对双方约定。">
    <div className="rounded-[10px] border border-[#E9E3D9] bg-white p-5">
      <strong data-testid="check-run-title" className="text-base">{title}</strong>
      <p role="status" data-testid="check-run-current-task" className="mt-2 text-sm text-[#587089]">{saved ? '报告已自动保存，可直接查看与导出。' : failed ? (error || '执行已中断，已有记录保留，请查看时间线。') : `${active}${batch}`}</p>
      {!saved && !failed && <div role="progressbar" aria-label="当前活动批次进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined} className="my-4 h-1.5 overflow-hidden rounded bg-[#EDE8DF]">
        <div className={`h-full bg-[#8B7355] ${percent == null ? 'bs-progress-stripes' : ''}`} style={{ width: `${percent ?? 100}%` }}/>
      </div>}
      <CheckRunStages stages={[
        { key: 'check', label: '合同与规范核查', detail: saved ? '执行已收尾' : failed ? '执行中断' : '核查进行中', done: saved, current: !saved && !failed },
        { key: 'report', label: '自动保存报告', detail: partial ? '部分结果，覆盖情况需区分查看' : saved ? '已保存' : '等待核查收尾', done: saved, current: !saved && progress?.phase === 'report' },
      ]}/>
      {partial && <p className="mt-3 text-sm text-[#92400E]">部分核查范围尚未完成，报告列示本次已有证据的问题，不能据此认定全部合规。</p>}
      <CheckRunTimeline events={events} failed={failed}/>
      <div className="mt-4 flex justify-end gap-3">
        <button type="button" onClick={onRefresh} className="rounded border px-3 py-2 text-sm">刷新状态</button>
        {failed && canResume && <button type="button" disabled={resuming} onClick={onResume} className="rounded bg-[#8B7355] px-4 py-2 text-sm text-white disabled:opacity-50">重新核查</button>}
        {saved && <button type="button" onClick={onOpenReport} className="rounded bg-[#8B7355] px-4 py-2 text-sm text-white">查看审查报告</button>}
      </div>
    </div>
  </CheckRunFrame>
}
