import { useMemo, useState } from 'react'
import { FileText } from 'lucide-react'
import { bsMaterialFileUrl } from '../lib/api'
import { STATUS_CODES, STATUS_WORD, statusLabelOf } from '../lib/statusDisplay'

// 结论三态配色（全站统一：不符合=红 / 符合=绿 / 无法核实=琥珀）。状态码不变，只换展示词。
export const STATUS_STYLE = {
  hit: { chip: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-600' },
  miss: { chip: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'bg-emerald-600' },
  undecidable: { chip: 'bg-amber-50 text-amber-800 border-amber-200', dot: 'bg-amber-500' },
}

// A-13（B2）严重度配色（与 STATUS_STYLE 并列；high=红 / medium=琥珀 / low=灰）。
// 未分级（历史结论或无 severity）不渲染徽标。
export const SEVERITY_STYLE = {
  high: { chip: 'bg-red-50 text-red-700 border-red-200', label: '高' },
  medium: { chip: 'bg-amber-50 text-amber-800 border-amber-200', label: '中' },
  low: { chip: 'bg-gray-50 text-gray-600 border-gray-200', label: '低' },
}

// 严重度排序权重（降序展示：high→medium→low→未分级），仅对已知档位生效。
const SEVERITY_RANK = { high: 0, medium: 1, low: 2 }

function bySeverity(a, b) {
  const ra = SEVERITY_RANK[a.severity] ?? 3
  const rb = SEVERITY_RANK[b.severity] ?? 3
  return ra - rb
}

// 结论分组（顺序即呈现顺序；kind 与后端 a1_check/a3_check/signature_chain 的 kind 字段一致。
// 分组标题按业主「五步审查」命名（FRS v2 §1）；内部功能编号 A1/A2/A3 不再对外展示。
// 新增后端 kind 必须同步登记这里，否则该类结论在界面静默丢弃——workload_ratio 曾漏）
export const KIND_SECTIONS = [
  { kind: 'completeness', title: '① 要素完整性核查', sub: '按 118 号文第五条申报材料逐项对表' },
  { kind: 'signature_chain', title: '① 要素完整性核查', sub: '报审签字链：法定链/集团链双轨 + 先报批后实施' },
  { kind: 'negative_list', title: '② 资质与内容合规性审查', sub: '负面清单情形（抽出数值再比，不凭描述命中）' },
  { kind: 'transfer_scope', title: '② 资质与内容合规性审查', sub: '转包情形（细则第十五条十类；缺事实证据判无法核实）' },
  { kind: 'illegal_subcontract', title: '② 资质与内容合规性审查', sub: '违法分包/视为违法/以劳务名义分包（细则第十六、十七条）' },
  { kind: 'qualification_match', title: '② 资质与内容合规性审查', sub: '资质与承揽范围匹配（只提示可能超范围，需人工核实）' },
  { kind: 'a1_dependency', title: '② 资质与内容合规性审查', sub: '监理审查与方案编制责任（证据取自报审结论）' },
  { kind: 'person_consistency', title: '③ 关键岗位人员核查', sub: '人员一致性 + 社保证明（分包合同/报审材料/名单/社保四方比对）' },
  { kind: 'cert_validity', title: '④ 证件证照有效性核查', sub: '营业执照/资质证书/安全生产许可证/三类人员证书有效期' },
  { kind: 'internal_records', title: '⑤ 内部信用与事故材料核查', sub: '仅核对本任务提供的业主内部记录，不访问外部网站' },
  // 后续功能（业主部署文件 二、（六）「分包合同总合同额匹配…预留接口」）：本期默认关闭，
  // 关闭时后端不产出该 kind 的条目；打开 data/config.json 的 enabled 开关才会出现在这里。
  { kind: 'amount_ratio', title: '后续功能 · 分包合同总合同额匹配', sub: '金额占比（本期未启用）' },
  { kind: 'workload_ratio', title: '后续功能 · 分包合同总合同额匹配', sub: '工程量占比（本期未启用）' },
  { kind: 'contract_semantic', title: '后续功能 · 合同语义核查', sub: '条款用词与文字细节（业主部署文件 二、（二）本期不纳入）' },
]

/**
 * 证据渲染（07 证据溯源三档语义，宁缺勿错）：
 * - precise：引文页内验中 → 可点，跳原件 #page=N
 * - page：页码可信但引文验不中 → 只展示页码文字，不可点
 * - unresolved / 无引文：不渲染定位控件
 */
function EvidenceCell({ finding, materialsById, onOpen }) {
  if (!finding.quote && finding.location_mode !== 'page') return null
  const mat = materialsById[finding.material_id]
  if (finding.location_mode === 'precise' && finding.material_id && mat) {
    return (
      <button
        onClick={() => onOpen(finding)}
        className="inline-flex items-center gap-1 rounded-[4px] border border-[#EFE6D8] bg-[#FDFAF4] px-1.5 py-0.5 text-xs text-[#8B7355] hover:bg-[#F7F1E9]"
        title={`在「${mat.file_name}」中查看原文`}
      >
        <FileText className="h-3 w-3" />
        查看证据{finding.page ? ` P${finding.page}` : ''}
      </button>
    )
  }
  // page 档：页码可信但引文没验中 → 只给页码，不给可点入口（不伪造精确定位）
  if (finding.location_mode === 'page' && finding.page) {
    return <span className="text-xs text-tertiary">第 {finding.page} 页</span>
  }
  return null
}

/* 复核回执行（P1-3 最小闭环）：未复核给「复核」入口；已复核显回执徽标，
   可再次复核覆盖（后端最后回执为准）。decision 二选一 + 意见选填。 */
function ReviewRow({ finding, onReview }) {
  const [editing, setEditing] = useState(false)
  const [decision, setDecision] = useState(null)
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const rv = finding.review

  // 仅问题项进入人工处置；符合项不显示处置控件。
  if (!['hit', 'undecidable'].includes(finding.status)) return null

  if (!editing && !rv) {
    return (
      <button onClick={() => setEditing(true)}
        className="mt-1 inline-flex items-center rounded-[4px] border border-[#EFE6D8] bg-[#FDFAF4] px-1.5 py-0.5 text-xs text-[#8B7355] hover:bg-[#F7F1E9]">
        处理
      </button>
    )
  }

  if (!editing && rv) {
    return (
      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
        <span className={`rounded border px-1.5 py-0.5 ${
          rv.decision === 'accepted' || rv.decision === 'confirm'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-red-200 bg-red-50 text-red-700'}`}>
          人工处置：{rv.decision_label}
        </span>
        {rv.note && <span className="min-w-0 text-[#6B7280]">「{rv.note}」</span>}
        <span className="tabular-nums text-tertiary">
          {rv.reviewer} · {(rv.at || '').replace('T', ' ')}
        </span>
        <button onClick={() => setEditing(true)} className="text-[#8B7355] hover:underline">
          修改处置
        </button>
      </p>
    )
  }

  return (
    <div className="mt-1.5 rounded-[6px] border border-[#EFE6D8] bg-[#FDFAF4] p-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {[['accepted', '采纳'], ['ignored', '忽略']].map(([k, label]) => (
          <button key={k} onClick={() => setDecision(k)}
            className={`rounded-[4px] border px-2 py-0.5 text-xs ${decision === k
              ? (k === 'accepted'
                  ? 'border-emerald-300 bg-emerald-50 font-medium text-emerald-700'
                  : 'border-red-300 bg-red-50 font-medium text-red-700')
              : 'border-[#E5E5E5] bg-white text-[#6B7280] hover:border-[#8B7355]'}`}>
            {label}
          </button>
        ))}
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="复核意见（选填）"
          className="min-w-0 flex-1 rounded-[4px] border border-[#E5E5E5] bg-white px-2 py-1 text-xs text-[#1A1A1A] placeholder:text-[#C0C0C0] focus:border-[#8B7355] focus:outline-none" />
        <button disabled={!decision || submitting}
          onClick={async () => {
            setSubmitting(true)
            try {
              await onReview(finding, decision, note.trim() || null)
              setEditing(false)
              setDecision(null)
              setNote('')
            } finally { setSubmitting(false) }
          }}
          className="rounded-[4px] bg-[#8B7355] px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60">
          {submitting ? '提交中…' : '提交回执'}
        </button>
        <button onClick={() => { setEditing(false); setDecision(null); setNote('') }}
          className="rounded-[4px] border border-[#E5E5E5] bg-white px-2 py-1 text-xs text-[#6B7280]">
          取消
        </button>
      </div>
    </div>
  )
}

export default function EvidencePanel({ findings, materialsById, onReview }) {
  const [modalFinding, setModalFinding] = useState(null)

  const grouped = useMemo(() => {
    const g = {}
    for (const f of findings || []) (g[f.kind] = g[f.kind] || []).push(f)
    return g
  }, [findings])

  const openEvidence = (f) => setModalFinding(f)
  const closeEvidence = () => setModalFinding(null)

  const modalMat = modalFinding ? materialsById[modalFinding.material_id] : null

  return (
    <div className="space-y-4">
      {KIND_SECTIONS.map(({ kind, title, sub }) => {
        const list = grouped[kind]
        if (!list || !list.length) return null
        // A-13（B2）：用户口径「报告按严重度排序」——high→medium→low→未分级（稳定排序保留原序）
        const ordered = [...list].sort(bySeverity)
        const counts = { hit: 0, miss: 0, undecidable: 0 }
        for (const f of list) counts[f.status] += 1
        return (
          <section key={kind} className="rounded-[6px] border border-[#E5E5E5] bg-white">
            <header className="flex items-baseline gap-2 border-b border-gray-100 px-4 py-2.5">
              <h3 className="text-sm font-semibold text-primary">{title}</h3>
              <span className="text-xs text-tertiary">{sub}</span>
              <span className="ml-auto text-xs tabular-nums text-tertiary">
                {STATUS_CODES.filter((code) => counts[code] > 0).map((code, index) => {
                  const tone = code === 'hit' ? 'text-red-600' : code === 'miss' ? 'text-emerald-600' : 'text-amber-600'
                  return (
                    <span key={code}>
                      {index > 0 ? ' · ' : ''}
                      <b className={tone}>{STATUS_WORD[code]} {counts[code]}</b>
                    </span>
                  )
                })}
              </span>
            </header>
            <ul>
              {ordered.map((f) => (
                <li key={f.id} className="flex items-start gap-2.5 border-b border-gray-50 px-4 py-2.5 last:border-b-0">
                  <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATUS_STYLE[f.status].dot}`} />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-1.5 text-sm leading-relaxed text-primary">
                      {SEVERITY_STYLE[f.severity] && (
                        <span className={`rounded border px-1.5 py-0.5 text-xs leading-none ${SEVERITY_STYLE[f.severity].chip}`}>
                          严重度：{SEVERITY_STYLE[f.severity].label}
                        </span>
                      )}
                      <span>{f.message}</span>
                    </p>
                    {f.source && <p className="mt-0.5 text-xs text-tertiary">依据：{f.source}</p>}
                    {onReview && <ReviewRow finding={f} onReview={onReview} />}
                  </div>
                  <div className="shrink-0 pt-0.5">
                    <EvidenceCell finding={f} materialsById={materialsById} onOpen={openEvidence} />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )
      })}
      {!findings || !findings.length ? (
        <div className="rounded-[6px] border border-dashed border-[#E5E5E5] bg-white px-4 py-10 text-center text-[13px] text-[#9CA3AF]">
          暂无核查结论——先在上面运行核查
        </div>
      ) : null}

      {/* 证据弹窗：引文 + 页码 + 原件跳页 */}
      {modalFinding && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6"
          onClick={closeEvidence}
        >
          <div
            className="flex max-h-[80vh] w-[560px] max-w-full flex-col overflow-hidden rounded-[8px] border border-[#E5E5E5] bg-white shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="flex items-center gap-2 border-b border-gray-100 px-4 py-3">
              <span className={`rounded px-1.5 py-0.5 text-xs border ${(STATUS_STYLE[modalFinding.status] || STATUS_STYLE.undecidable).chip}`}>
                {statusLabelOf(modalFinding)}
              </span>
              <span className="truncate text-sm font-medium text-primary">
                {modalMat ? modalMat.file_name : '证据'}
              </span>
              <button onClick={closeEvidence} className="ml-auto px-1 text-lg leading-none text-tertiary hover:text-primary">×</button>
            </header>
            <div className="flex-1 overflow-auto px-4 py-3">
              <p className="whitespace-pre-wrap rounded-lg bg-gray-50 px-3 py-2.5 text-sm leading-relaxed text-primary">
                {modalFinding.quote}
              </p>
              {modalFinding.page && (
                <p className="mt-2 text-xs text-tertiary">原文第 {modalFinding.page} 页</p>
              )}
            </div>
            <footer className="flex items-center gap-2 border-t border-gray-100 bg-gray-50 px-4 py-2.5">
              {modalFinding.location_mode === 'precise' && modalMat ? (
                <a
                  href={bsMaterialFileUrl(modalFinding.material_id, modalFinding.page)}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-[6px] bg-[#8B7355] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                >
                  打开原件{modalFinding.page ? `并定位到第 ${modalFinding.page} 页` : ''}
                </a>
              ) : (
                <span className="text-xs text-tertiary">该结论暂无精确定位（证据未在文本层验中，不给幽灵页）</span>
              )}
              <button onClick={closeEvidence} className="ml-auto rounded-md border border-gray-200 px-3 py-1.5 text-xs text-secondary hover:bg-gray-100">
                关闭
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  )
}
