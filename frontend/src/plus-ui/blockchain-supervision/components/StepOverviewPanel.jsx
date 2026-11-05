// 刀F（交互重做第一步）：五步审查总览。
//
// 用户反馈（2026-10-25）：「在某一步里缺了哪些文件，要依次列出来」；当前只有计数。
// 本组件把 `lib/stepOverviewModel` 的只读派生结果按步骤展开：
//   第 1 步 = 状态 + **缺件清单（按逻辑项/子件）**；后续步骤只展示主要问题（带规则依据）+ 进入结论列表。
// 缺件行的「补件」复用既有补充材料语义（带材料类进入材料阶段），不新造上传接口；
// 无可补类别的缺件如实显示「需人工核实」，不给假按钮。
//
// 视觉口径沿用 88：中性浅灰底 + 深蓝结构色；问题行不重复渲染机器状态/严重度，
// 状态计数保留在步骤汇总，详细依据与人工动作进入核查项详情。

import { useState } from 'react'

const ORDINALS = ['①', '②', '③', '④', '⑤']

const STATE_TONES = {
  completed: 'border-[#CFE0F0] bg-[#EAF2FB] text-[#143B63]',
  running: 'border-[#B8C9D9] bg-[#F1F6FB] text-[#143B63]',
  not_started: 'border-[#E2E8F0] bg-white text-[#64748B]',
  out_of_scope: 'border-dashed border-[#CBD5E1] bg-[#F8FAFC] text-[#94A3B8]',
}

function stateTone(state) {
  return STATE_TONES[state] || 'border-[#D8E1EB] bg-white text-[#64748B]'
}

function ordinalFor(index) {
  const value = Number(index)
  return Number.isInteger(value) && value >= 1 && value <= ORDINALS.length ? ORDINALS[value - 1] : `${index}.`
}

function basisTags(basis) {
  if (!basis) return null
  const tags = []
  if (basis.docNo) tags.push(basis.docNo)
  if (basis.marker) tags.push(basis.marker)
  if (!tags.length && basis.clauseId) tags.push(basis.clauseId)
  return tags
}

function stepSummaryText(step) {
  if (step.state === 'out_of_scope') return '本次不适用'
  if (step.index === 1) return `缺 ${step.missingCount} 项 · 其余材料见材料清单`
  return [
    '',
    '',
    '仅展示规则异常 · 关联材料回链',
    '仅展示人员一致性异常',
    '仅展示证照异常',
    '仅展示命中问题与证据说明',
  ][step.index] || '仅展示规则异常 · 关联材料回链'
}

function StepMissingRow({ item, onSupplement }) {
  return (
    <li
      data-testid={`step-overview-missing-${item.label}`}
      className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[#EEF2F7] px-3 py-2 last:border-b-0"
    >
      <span className="min-w-0 flex-1 break-words text-[12px] text-[#16283D]" title={item.label}>
        {item.label}
      </span>
      {item.categoryLabel && (
        <span className="shrink-0 rounded-full border border-[#D8E1EB] bg-[#F8FAFC] px-2 py-0.5 text-[10px] text-[#64748B]">
          {item.categoryLabel}
        </span>
      )}
      <span className="shrink-0 text-[11px] tabular-nums text-[#92400E]">影响 {item.count} 条</span>
      {item.actionable ? (
        <button
          type="button"
          data-testid={`step-overview-supplement-${item.label}`}
          onClick={() => onSupplement?.(item.supplement)}
          className="min-h-11 shrink-0 rounded-[4px] border border-[#8B7355] bg-white px-2 py-1 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63]"
        >
          补件
        </button>
      ) : (
        <span className="shrink-0 rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-0.5 text-[10px] text-[#92400E]">
          需人工核实
        </span>
      )}
    </li>
  )
}

function StepIssueRow({ issue, onSelectFinding }) {
  const tags = basisTags(issue.basis)
  return (
    <li className="min-w-0 border-b border-[#EEF2F7] px-3 py-2 last:border-b-0">
      <button
        type="button"
        data-testid={`step-overview-issue-${issue.id}`}
        onClick={() => onSelectFinding?.(issue.id)}
        className="block min-h-11 w-full min-w-0 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63]"
      >
        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
          {issue.itemId && <span className="shrink-0 text-[10px] tabular-nums text-[#94A3B8]">{issue.itemId}</span>}
        </span>
        <span className="mt-1 line-clamp-2 block min-w-0 break-words text-[12px] leading-relaxed text-[#385A77]" title={issue.message || undefined}>
          {issue.message || '（该结论没有可展示的问题描述）'}
        </span>
        <span className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
          {tags && tags.length > 0 ? (
            tags.map((tag) => (
              <span key={tag} className="rounded bg-[#F7F1E9] px-1.5 py-0.5 text-[10px] text-[#8B7355]">
                {tag}
              </span>
            ))
          ) : (
            <span className="text-[10px] text-[#94A3B8]">未建立条款坐标</span>
          )}
          {issue.basis?.humanVerified && (
            <span className="text-[10px] text-[#2E7D32]">已人工校对</span>
          )}
        </span>
      </button>
    </li>
  )
}

function StepSection({ step, onSelectStep, onSupplement, onSelectFinding }) {
  const outOfScope = step.state === 'out_of_scope'
  const showMissing = step.index === 1 && !outOfScope
  // 用本地状态承接 <details> 的展开：只决定初始展开，不把用户后续折叠状态覆盖回去。
  const [open, setOpen] = useState(step.index === 1)
  return (
    <details
      data-testid={`step-overview-${step.stepId}`}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className={`min-w-0 rounded-[6px] border bg-white ${outOfScope ? 'border-dashed border-[#CBD5E1]' : 'border-[#D8E1EB]'}`}
    >
      <summary className="flex min-h-11 min-w-0 cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] [&::-webkit-details-marker]:hidden">
        <span className="shrink-0 text-[13px] leading-5 text-[#143B63]">{ordinalFor(step.index)}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-[#16283D]" title={step.title}>
          {step.title}
        </span>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${stateTone(step.state)}`}>
          {step.index === 1 ? '材料入口' : `${step.issues.length} 个问题`}
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-[#64748B]">
          {stepSummaryText(step)}
        </span>
      </summary>

      <div className="border-t border-[#EEF2F7] px-3 py-2.5">
        <p className="mb-2 text-[11px] leading-relaxed text-[#587089]">
          {step.index === 1 ? '本步骤是唯一的材料完整性入口；后续步骤只引用这里的材料。' : '本步骤只展示规则异常、依据和证据，不重复材料缺件清单。'}
        </p>
        {showMissing && <div className="min-w-0">
          <h4 data-testid={`step-overview-missing-heading-${step.stepId}`} className="flex flex-wrap items-center gap-2 text-[11px] font-semibold text-[#16283D]">
            缺件清单
            <span className="rounded-full bg-[#FDF3F3] px-1.5 py-0.5 text-[10px] tabular-nums text-[#C62828]">
              {step.missingCount} 项
            </span>
            {step.manualOnly > 0 && (
              <span className="text-[10px] font-normal text-[#92400E]">另有 {step.manualOnly} 条需人工核实（无缺件标签）</span>
            )}
          </h4>
          {step.missingItems.length > 0 ? (
            <ul data-testid={`step-overview-missing-list-${step.stepId}`} className="mt-1.5 overflow-hidden rounded-[4px] border border-[#EEF2F7]">
              {step.missingItems.map((item) => (
                <StepMissingRow key={item.label} item={item} onSupplement={onSupplement} />
              ))}
            </ul>
          ) : (
            <p data-testid={`step-overview-missing-empty-${step.stepId}`} className="mt-1.5 rounded-[4px] border border-dashed border-[#E2E8F0] bg-[#F8FAFC] px-2.5 py-2 text-[11px] leading-relaxed text-[#64748B]">
              {outOfScope ? '本次不适用，无缺件。' : '本步没有可归类的缺件。'}
              {step.manualOnly > 0 ? ' 无法核实项没有缺件标签，请在结论详情里按「需人工核实」处理。' : ''}
            </p>
          )}
        </div>}

        {!showMissing && <div className="min-w-0">
          <h4 className="text-[11px] font-semibold text-[#16283D]">主要问题</h4>
          {step.issues.length > 0 ? (
            <ul data-testid={`step-overview-issues-${step.stepId}`} className="mt-1.5 overflow-hidden rounded-[4px] border border-[#EEF2F7]">
              {step.issues.map((issue) => (
                <StepIssueRow key={issue.id} issue={issue} onSelectFinding={onSelectFinding} />
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 rounded-[4px] border border-dashed border-[#E2E8F0] bg-[#F8FAFC] px-2.5 py-2 text-[11px] text-[#64748B]">
              本步暂无不符/无法核实结论。
            </p>
          )}
        </div>}

        <div className="mt-2.5 flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            data-testid={`step-overview-open-${step.stepId}`}
            onClick={() => onSelectStep?.(step.stepId)}
            className="min-h-11 rounded-[4px] border border-[#8B9DB0] bg-white px-2.5 text-[11px] text-[#143B63] hover:bg-[#F1F6FB] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63]"
          >
            查看本步问题
          </button>
        </div>
      </div>
    </details>
  )
}

export default function StepOverviewPanel({
  model = null,
  loading = false,
  error = null,
  onSelectStep,
  onSupplement,
  onSelectFinding,
  onOpenUpload,
}) {
  const steps = Array.isArray(model?.steps) ? model.steps : []
  const totals = model?.totals || {}

  return (
    <section data-testid="step-overview" aria-label="五步审查" className="min-w-0 rounded-[9px] border border-[#DCE5EE] bg-white">
      <header className="flex min-w-0 flex-wrap items-start gap-2 border-b border-[#DCE5EE] px-3.5 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold text-[#16283D]">五步审查</h2>
          <p className="mt-0.5 text-[11px] leading-relaxed text-[#64748B]">
            材料只在第 1 步集中展示
          </p>
        </div>
        {onOpenUpload && (
          <button
            type="button"
            data-testid="step-overview-upload"
            onClick={onOpenUpload}
            className="min-h-11 shrink-0 rounded-[4px] border border-[#8B7355] bg-white px-2.5 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63]"
          >
            上传 / 补件
          </button>
        )}
      </header>

      {loading ? (
        <p data-testid="step-overview-loading" className="m-3 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-6 text-center text-[12px] text-[#64748B]">
          正在读取五步结果与第 1 步缺件…
        </p>
      ) : steps.length === 0 ? (
        <p data-testid="step-overview-empty" className="m-3 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-6 text-center text-[12px] leading-relaxed text-[#64748B]">
          {error || '五步结果尚未就绪。核查跑完后，这里只在第 1 步列出缺件，后续步骤展示主要问题。'}
        </p>
      ) : (
        <>
          <div data-testid="step-overview-summary" className="mx-3 mt-3 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-[11px] text-[#64748B]">
            <span>结论 <b className="tabular-nums text-[#16283D]">{totals.total ?? 0}</b> 条</span>
            <span>通过 <b className="tabular-nums text-[#16283D]">{totals.miss ?? 0}</b></span>
            <span>待处理 <b className="tabular-nums text-[#8B7355]">{(totals.hit ?? 0) + (totals.undecidable ?? 0)}</b></span>
            <span
              title="第 1 步内按缺失项/子件去重，不等于文件或纸质份数；后续步骤不重复生成材料缺件"
            >
              第 1 步缺件项 <b className="tabular-nums text-[#C62828]">{totals.missingByStep ?? 0}</b>
              <span className="ml-1 text-[10px] text-[#94A3B8]" data-testid="step-overview-missing-note">
                按缺失项/子件去重，不等于文件或纸质份数；后续步骤只看异常
              </span>
            </span>
            {totals.openTerm > 0 && <span>仅登记 <b className="tabular-nums text-[#8B7355]">{totals.openTerm}</b> 条</span>}
            {totals.manualOnly > 0 && <span>需人工核实 <b className="tabular-nums text-[#92400E]">{totals.manualOnly}</b> 条</span>}
          </div>

          <div className="mt-2 flex min-w-0 flex-col gap-2 px-3 pb-3">
            {steps.map((step) => (
              <StepSection
                key={step.stepId}
                step={step}
                onSelectStep={onSelectStep}
                onSupplement={onSupplement}
                onSelectFinding={onSelectFinding}
              />
            ))}
          </div>

          {model?.unassigned > 0 && (
            <p data-testid="step-overview-unassigned" className="mx-3 mb-3 mt-2 rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-1.5 text-[11px] leading-relaxed text-[#64748B]">
              另有 {model.unassigned} 条结论不属于五步任意一步（如后续功能项），可在「核查项」的「全部」中查看；本页合计不等于全部结论数。
            </p>
          )}
        </>
      )}
    </section>
  )
}
