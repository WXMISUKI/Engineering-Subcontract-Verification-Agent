import { useMemo } from 'react'
import { AlertTriangle, ChevronDown } from 'lucide-react'

import { buildChecklistSummaryModel } from '../lib/checklistSummaryModel'
import { subjectCodeLabel } from '../lib/materialState'

const TYPE_LABELS = {
  professional: '专业分包',
  labor: '劳务分包',
}

function CountCell({ label, value, tone = 'normal', detail = null }) {
  const tones = {
    normal: 'text-[#16283D]',
    good: 'text-[#2E7D32]',
    bad: 'text-[#C62828]',
    warn: 'text-[#92400E]',
    muted: 'text-[#64748B]',
  }
  return (
    <div className="min-w-0 rounded-[6px] bg-[#F8FAFC] px-2.5 py-2">
      <dt className="text-[11px] leading-4 text-[#64748B]">{label}</dt>
      <dd className={`mt-0.5 text-[16px] font-semibold leading-5 tabular-nums ${tones[tone]}`}>
        {value}
        {detail && <span className="ml-1 text-[10px] font-normal text-[#64748B]">{detail}</span>}
      </dd>
    </div>
  )
}

function SubjectCard({ subject }) {
  const titleId = `subject-checklist-title-${subject.subject_id}`
  const codeLabel = subjectCodeLabel(subject.stable_code)
  const displayName = subject.name || codeLabel
  const typeLabel = TYPE_LABELS[subject.subject_type] || '类型待确认'
  // 编号与类型分列：编号是身份（创建时取自当时类型的前缀，之后不变），
  // 类型以 `subject_type` 为准，二者不并排成「编号 · 类型」以免自相矛盾。
  const metaLine = subject.name ? `${codeLabel} · ${typeLabel}` : typeLabel
  const ready = subject.status === 'ready'

  return (
    <article
      data-testid={`subject-checklist-${subject.subject_id}`}
      aria-labelledby={titleId}
      className="min-w-0 rounded-[8px] border border-[#D8E1EB] bg-white p-3"
    >
      <header className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="truncate text-[13px] font-semibold text-[#16283D]" title={displayName}>
            {displayName}
          </h3>
          <p className="mt-0.5 text-[11px] text-[#64748B]">
            {metaLine}{subject.profile_codes.length > 0 && ` · ${subject.profile_codes.join('、')}`}
          </p>
        </div>
        {ready && (
          <p className="shrink-0 rounded-full border border-[#C9D7E5] bg-[#F3F7FB] px-2.5 py-1 text-[11px] text-[#143B63]">
            本次适用 <b className="tabular-nums">{subject.checklist_summary.applicable_total}</b> 项
          </p>
        )}
      </header>

      {subject.type_conflict && (
        <p
          data-testid={`subject-checklist-type-notice-${subject.subject_id}`}
          className="mt-2 flex gap-2 rounded-[6px] border border-[#E7C98B] bg-[#FFF9ED] px-3 py-2 text-[11px] leading-relaxed text-[#7C4A03]"
        >
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            <b>类型证据提示：</b>
            {subject.type_conflict_reason || 'AI 识别证据与所选类别不一致'}。
            已按所选类别核查；如归类有误，可在材料台账中更正。
          </span>
        </p>
      )}

      {subject.status === 'type_pending' && (
        <p
          data-testid={`subject-checklist-pending-${subject.subject_id}`}
          className="mt-2 rounded-[6px] border border-[#D8E1EB] bg-[#F8FAFC] px-3 py-2 text-[12px] leading-relaxed text-[#4B5563]"
        >
          <b>主体类型待确认：</b>{subject.status_reason}。确认前不展示专业/劳务基础分母。
        </p>
      )}

      {ready && (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <CountCell label="基础清单" value={subject.checklist_summary.base_total} />
            <CountCell label="已齐" value={subject.checklist_summary.present_total} tone="good" />
            <CountCell label="不符合" value={subject.checklist_summary.nonconforming_total} tone="bad" />
            <CountCell label="其中缺件" value={subject.checklist_summary.missing_total} tone="bad" />
            <CountCell label="无法核实" value={subject.checklist_summary.undecidable_total} tone="warn" />
            <CountCell label="待核" value={subject.checklist_summary.pending_total} tone="normal" />
          </dl>
        </>
      )}

      {subject.procedural_hints.length > 0 && (
        <details
          data-testid={`subject-checklist-hints-${subject.subject_id}`}
          className="mt-2 rounded-[6px] border border-[#E2E8F0] bg-[#FAFBFC]"
        >
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 text-[11px] text-[#4B5563] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] [&::-webkit-details-marker]:hidden">
            <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-[#64748B]" />
            <span>流程提示 {subject.procedural_hints.length} 条（不计入电子缺件）</span>
          </summary>
          <ul className="border-t border-[#E8EEF4] px-4 py-2 text-[11px] leading-relaxed text-[#64748B]">
            {subject.procedural_hints.map((hint) => <li key={hint.code} className="py-1">{hint.text}</li>)}
          </ul>
        </details>
      )}
    </article>
  )
}

export default function SubjectChecklistSummary({ summary, loading = false }) {
  const model = useMemo(() => buildChecklistSummaryModel(summary), [summary])

  return (
    <section
      data-testid="subject-checklist-summary"
      aria-labelledby="subject-checklist-summary-title"
      className="subject-board min-w-0 rounded-[9px] border border-[#DCE5EE] bg-white"
    >
      <header className="flex min-w-0 items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 id="subject-checklist-summary-title" className="text-[14px] font-semibold text-[#16283D]">
            主体级清单摘要
          </h2>
          <p className="mt-0.5 text-[11px] leading-relaxed text-[#64748B]">
            按主体逻辑核查项计数，不代表电子文件或纸质份数
          </p>
        </div>
        <span className="shrink-0 text-[11px] text-[#8291A1]">按主体展示附件要求</span>
      </header>

      {loading && summary == null ? (
        <p role="status" aria-live="polite" className="mt-3 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-5 text-center text-[12px] text-[#64748B]">
          正在读取主体清单摘要…
        </p>
      ) : model.kind !== 'ready' ? (
        <p
          data-testid="subject-checklist-fallback"
          role="status"
          className="mt-3 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-3 text-[12px] leading-relaxed text-[#64748B]"
        >
          {model.message}
        </p>
      ) : model.subjects.length === 0 ? (
        <p className="mt-3 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-3 text-[12px] text-[#64748B]">
          当前没有可展示的分包主体摘要。
        </p>
      ) : (
        <div className="subject-grid mt-0 grid min-w-0 gap-2 border-t border-[#DCE5EE] p-3 lg:grid-cols-2">
          {model.subjects.map((subject) => <SubjectCard key={subject.subject_id} subject={subject} />)}
        </div>
      )}

      {model.kind === 'ready' && model.guidanceNotes.length > 0 && (
        <details
          data-testid="subject-checklist-guidance"
          className="mt-2 rounded-[6px] border border-[#E2E8F0] bg-white"
        >
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 text-[12px] text-[#4B5563] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] [&::-webkit-details-marker]:hidden">
            <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-[#64748B]" />
            <span>项目级办理提示 {model.guidanceNotes.length} 条（不计入电子缺件）</span>
          </summary>
          <ul className="border-t border-[#E8EEF4] px-4 py-2 text-[12px] leading-relaxed text-[#64748B]">
            {model.guidanceNotes.map((note) => (
              <li key={note.code} className="py-1">
                <b className="font-medium text-[#4B5563]">{note.label}</b>：{note.detail}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
