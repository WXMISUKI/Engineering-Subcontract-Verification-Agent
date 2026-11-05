// 五步审查卡（V2-b）：纯展示组件。
// - 只吃 props（steps / loading / selectedStepId / onSelectStep），不请求、不判定、
//   不 import 后端映射常量；状态文案与计数全部由上游派生层给出。
// - 视觉口径（docs 88 原型验收清单）：中性浅灰底 + 深蓝结构色；绿/琥珀/红只留给业务结论；
//   状态一律「文字 + 颜色」双通道，out_of_scope 额外用虚线 + 弱化色。
// - 卡片是 <button type="button">，保证键盘可达；aria-pressed 表达选中。
// - 窄屏单列、宽屏多列，根与卡片均 min-w-0，避免页面级横向溢出。

import { countOf } from '../lib/statusDisplay'

const ORDINALS = ['①', '②', '③', '④', '⑤']

function ordinalFor(index) {
  const value = Number(index)
  return Number.isInteger(value) && value >= 1 && value <= ORDINALS.length ? ORDINALS[value - 1] : `${index}.`
}

// 步骤进度属于结构色，不用绿/琥珀/红；四种状态各自可区分，out_of_scope 虚线弱化。
const STATE_TONES = {
  completed: 'border-[#CFE0F0] bg-[#EAF2FB] text-[#143B63]',
  running: 'border-[#B8C9D9] bg-[#F1F6FB] text-[#143B63]',
  not_started: 'border-[#E2E8F0] bg-white text-[#64748B]',
  out_of_scope: 'border-dashed border-[#CBD5E1] bg-[#F8FAFC] text-[#94A3B8]',
}

const NEUTRAL_TONE = 'border-[#D8E1EB] bg-white text-[#64748B]'

// 保留需要用户处理的运行异常，隐藏参考文件性质等重复说明。
function actionableLimitation(message) {
  return /失败|未完成|未执行|处理中|不可用|超时/.test(String(message))
}

function stateTone(state) {
  return STATE_TONES[state] || NEUTRAL_TONE
}

function countLine(counts) {
  const source = counts && typeof counts === 'object' ? counts : {}
  const total = Number(source.total) || 0
  const passed = countOf(source, 'miss')
  const pending = countOf(source, 'open')
  return `共 ${total} 项 · 通过 ${passed} · 待处理 ${pending}`
}

function detailLine(step) {
  const missing = Number(step?.missingCount) || 0
  const manual = Number(step?.manualOnly) || 0
  const issues = Number(step?.issueCount) || (Array.isArray(step?.issues) ? step.issues.length : 0)
  if (step?.index === 1 && (missing > 0 || manual > 0)) {
    return `册内未见 ${missing} 项 · 随表核验 ${manual} 项`
  }
  if (issues > 0) return `主要问题 ${issues} 项，可点击查看`
  if (step?.index === 5 && step?.state === 'completed' && step?.counts?.total === 0) return '未提供内部材料'
  return '点击查看本步骤内容'
}

export default function FiveStepCards({
  steps = [],
  loading = false,
  selectedStepId = null,
  onSelectStep,
  className = '',
  compact = false,
  // 不属于五步任意一步的结论条数（如后续功能项）。默认为 0：不渲染脚注，不制造噪音。
  // 只展示计数，不猜它属于哪一步——避免「卡片总和」与「结论总数」不一致而无解释。
  unassigned = 0,
}) {
  const visibleSteps = (Array.isArray(steps) ? steps : []).filter(Boolean)
  const allSelected = selectedStepId == null
  const completedSteps = visibleSteps.filter((step) => ['completed', 'out_of_scope'].includes(step.state)).length

  return (
    <section data-testid="five-step-cards" aria-label="五步审查" className={`w-full min-w-0 rounded-[6px] bg-[#FBF5EC] p-3 ${className}`}>
      <header className="mb-2 flex min-w-0 items-center gap-2">
        <h2 className="text-[13px] font-semibold text-[#16283D]">五步审查</h2>
        {visibleSteps.length > 0 && (
          <>
          <span data-testid="five-step-progress" className="ml-auto rounded-full border border-[#D9C4A4] bg-[#FBF5EC] px-2 py-0.5 text-[10px] font-medium tabular-nums text-[#8B7355]">
            {completedSteps} / {visibleSteps.length} 步
          </span>
          <button
            type="button"
            data-testid="five-step-all"
            aria-pressed={allSelected}
            onClick={() => onSelectStep?.(null)}
            className={`min-h-8 shrink-0 rounded-[5px] border px-2.5 text-[11px] transition-colors ${
              allSelected
                ? 'border-[#B8C9D9] bg-[#EAF2FB] font-medium text-[#143B63]'
                : 'border-[#D8E1EB] bg-white text-[#64748B] hover:bg-[#F8FAFC]'
            }`}
          >
            全部步骤
          </button>
          </>
        )}
      </header>

      {visibleSteps.length === 0 ? (
        <p
          data-testid="five-step-empty"
          className="rounded-[6px] border border-dashed border-[#D8E1EB] bg-[#F8FAFC] px-3 py-6 text-center text-[12px] text-[#64748B]"
        >
          {loading ? '正在读取五步结果…' : '五步结果尚未就绪'}
        </p>
      ) : (
        <div className={`grid min-w-0 gap-2 ${compact ? 'grid-cols-1' : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5'}`}>
          {visibleSteps.map((step, position) => {
            const stepId = step.stepId ?? `step-${position + 1}`
            const selected = selectedStepId != null && selectedStepId === stepId
            const limitations = (Array.isArray(step.limitations) ? step.limitations : []).filter(actionableLimitation)
            const outOfScope = step.state === 'out_of_scope'

            return (
              <button
                key={stepId}
                type="button"
                data-testid={`five-step-card-${stepId}`}
                data-step-state={step.state}
                aria-pressed={selected}
                onClick={() => onSelectStep?.(selected ? null : stepId)}
                className={`flex min-h-[96px] min-w-0 flex-col gap-1 rounded-[4px] border p-2 text-left transition-colors ${
                  outOfScope ? 'border-dashed' : ''
                } ${
                  selected
                    ? 'border-[#8B7355] bg-[#FBF5EC] ring-1 ring-inset ring-[#D9C4A4]'
                    : 'border-[#E5DED3] bg-white hover:border-[#C9B18F] hover:bg-[#FFFCF8]'
                }`}
              >
                <span className="flex min-w-0 items-start gap-1.5">
                  <span className="shrink-0 text-[13px] leading-5 text-[#143B63]">{ordinalFor(step.index ?? position + 1)}</span>
                  <span className="min-w-0 flex-1 truncate text-[12px] font-semibold leading-5 text-[#16283D]" title={step.title}>
                    {step.title}
                  </span>
                </span>

                <span
                  data-testid={`five-step-state-${stepId}`}
                  className={`inline-flex w-fit max-w-full items-center rounded-full border px-2 py-0.5 text-[10px] ${stateTone(step.state)}`}
                >
                  <span className="truncate">{step.stateLabel || '状态未标注'}</span>
                </span>

                <span className="text-[10px] leading-relaxed text-[#64748B]">{countLine(step.counts)}</span>
                <span className="truncate text-[10px] leading-relaxed text-[#385A77]" title={detailLine(step)}>{detailLine(step)}</span>

                {limitations.length > 0 && <span className="min-w-0 truncate text-[10px] text-[#92400E]" data-testid={`five-step-limitation-${stepId}`} title={limitations.join('；')}>{limitations[0]}{limitations.length > 1 ? ` 等 ${limitations.length} 条` : ''}</span>}

              </button>
            )
          })}
        </div>
      )}

      {visibleSteps.length > 0 && unassigned > 0 && (
        <p
          data-testid="five-step-unassigned"
          className="mt-2 rounded-[6px] border border-dashed border-[#D8E1EB] bg-[#F8FAFC] px-3 py-1.5 text-[11px] leading-relaxed text-[#64748B]"
        >
          另有 {unassigned} 条结论不属于五步任意一步（如后续功能项），可在左侧「全部」中查看；本卡合计不等于全部结论数。
        </p>
      )}
    </section>
  )
}
