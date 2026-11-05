import { useState } from 'react'
import { GROUP_LABELS } from '../lib/fiveStepModel'

const STEP_STATE = { completed: '依据已记录', running: '处理中', not_started: '尚未执行', out_of_scope: '本次未启用' }
const RUN_STATE = { completed: '已完成', running: '处理中', not_started: '尚未执行', failed: '未完成', partial: '部分完成' }

function pageLabel(item) {
  const start = item.physical_page ?? item.page_start
  const end = item.physical_page ? item.physical_page_end : item.page_end
  if (!Number.isInteger(start) || start < 1) return '原件页码未登记'
  return `原件第 ${start}${Number.isInteger(end) && end > start ? `–${end}` : ''} 页`
}

function Limitations({ items = [] }) {
  const messages = [...new Set(items.map((item) => item.message).filter(Boolean))]
  return messages.length > 0 && <ul className="space-y-1 text-[12px] leading-relaxed text-[#92400E]">
    {messages.map((message) => <li key={message}>{message}</li>)}
  </ul>
}

function RunSummary({ runs = [] }) {
  if (!runs.length) return null
  return <section data-testid="reference-run-summary" className="space-y-1 border-b border-[#F3EDE3] pb-2">
    <h3 className="text-[12px] font-medium text-[#8B7355]">本代分组执行</h3>
    <ul className="space-y-1 text-[12px] leading-relaxed text-[#6B7280]">
      {runs.map((run) => <li key={`${run.group_id}-${run.run_id || 'pending'}`}>
        {GROUP_LABELS[run.group_id] || '核查分组'}：{run.execution_mode === 'carried_forward' ? '沿用上一轮已完成结果（本轮未重新查询）' : (RUN_STATE[run.state] || '状态待确认')}
      </li>)}
    </ul>
  </section>
}

function Source({ item, online, onDownload }) {
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const marker = item.marker || item.clause_id?.split('#').pop()
  const download = async () => {
    setDownloading(true)
    setError('')
    try { await onDownload(item) } catch (e) { setError(e?.message || '规范原件暂不可用，请稍后重试') }
    finally { setDownloading(false) }
  }
  return <li className="space-y-1 border-l-2 border-[#DED2C0] pl-3 text-[12px] leading-relaxed">
    <p className="break-words font-medium text-[#4B5563]">{item.doc_title || '固定规范'}{item.doc_no ? `（${item.doc_no}）` : ''}{marker ? ` · ${marker}` : ''}{item.list_no != null ? ` · 第 ${item.list_no} 项` : ''}</p>
    {item.available === false ? <p className="text-[#92400E]">固定依据尚未登记完整，请人工核对原件。</p> : <>
      <p className="text-[#9CA3AF]">{pageLabel(item)} · {item.human_verified ? '已人工校对' : '尚未人工校对'}</p>
      {online && item.subject_types?.length > 0 && <p className="text-[#9CA3AF]">适用主体类型：{item.subject_types.map((type) => ({ professional: '专业分包', labor: '劳务分包' }[type] || '类型待核实')).join('、')}</p>}
      {online && item.reference_completeness === 'partial' && <p className="text-[#92400E]">仅为部分原文，请核对完整要求</p>}
      <p className="whitespace-pre-wrap break-words text-[#6B7280]">{online ? item.quote : item.text}</p>
    </>}
    {online && item.reference_id && onDownload && <button type="button" disabled={downloading} onClick={download} className="min-h-8 rounded border border-[#DED2C0] px-2 text-[#8B7355] hover:bg-[#F7F1E9] disabled:opacity-60">{downloading ? '下载中…' : '下载规范原件'}</button>}
    {error && <p role="alert" className="text-[#B45309]">{error}</p>}
  </li>
}

// 只渲染调用方提供的冻结快照；历史缺失时不读取当前规则或当前任务作为替代。
export default function ReferenceSnapshot({ snapshot, historical = false, onDownload }) {
  return <details data-testid="reference-snapshot" className="max-h-[45vh] shrink-0 overflow-y-auto rounded-[6px] border border-[#E5E5E5] bg-white px-4 py-3">
    <summary className="cursor-pointer select-none text-[13px] font-medium text-[#5B4A2F]">规范依据与查询说明</summary>
    {!snapshot ? <p className="mt-2 text-[12px] text-[#9CA3AF]">{historical ? '该历史版本未保存规范依据快照，无法还原当时的依据。' : '本次核查尚无规范依据快照；旧任务需重新核查后记录。'}</p> : <div className="mt-2 space-y-2 border-t border-[#F3EDE3] pt-2">
      <p className="text-[12px] leading-relaxed text-[#6B7280]">以下为{historical ? '该历史版本' : '本轮核查'}保存的依据。材料证据在各核查项中单独展示；在线参考不替代固定规则及核查结论。</p>
      <RunSummary runs={snapshot.runs} />
      <Limitations items={snapshot.limitations} />
      {(snapshot.steps || []).map((step, index) => <details key={step.step_id} data-testid={`reference-step-${step.step_id}`} className="rounded border border-[#EFE6D8] px-3 py-2">
        <summary className="cursor-pointer text-[12px] text-[#4B5563]"><span className="mr-1 text-[#9CA3AF]">{index + 1}.</span> <span>{step.title}</span><span className="ml-2 text-[#9CA3AF]">{STEP_STATE[step.state] || '状态待确认'}</span></summary>
        <div className="mt-2 space-y-3">
          <section><h3 className="mb-2 text-xs font-medium text-[#8B7355]">固定审查依据</h3>
            <ul className="space-y-3">{(step.fixed_basis || []).map((item, idx) => <Source key={`${item.clause_id}-${item.list_no ?? idx}`} item={item} />)}</ul>
            {!(step.fixed_basis || []).length && <p className="text-[12px] text-[#9CA3AF]">本步骤未保存固定依据。</p>}
          </section>
          <section><h3 className="mb-2 text-xs font-medium text-[#8B7355]">在线参考原文</h3>
            <ul className="space-y-3">{(step.references || []).map((item) => <Source key={item.reference_id} item={item} online onDownload={onDownload} />)}</ul>
            {!(step.references || []).length && <p className="text-[12px] text-[#9CA3AF]">本步骤未保存在线参考原文，具体原因见查询说明。</p>}
          </section>
          <Limitations items={step.limitations} />
        </div>
      </details>)}
    </div>}
  </details>
}
