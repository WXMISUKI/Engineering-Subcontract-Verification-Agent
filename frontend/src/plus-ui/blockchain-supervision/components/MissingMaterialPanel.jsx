// 待补材料面板（刀A / BS-P1-06）：纯展示组件。
//
// - 只吃 props（plan / loading / error / selectedCategory / onSelectCategory / onSupplement），
//   不请求、不聚合缺件、不判断材料类——分桶与计数来自 lib/todoPlan 对后端 /todo 的展示分区。
// - 「可补件」每行 = 一个材料类：说明「补这一份可消几条」并直达补件（复用 material-edit）；
//   「非补件缺口」（兜底条款 / 审查产出或人工作判）不提供补件按钮——补件补不上，不能假装能补。
// - 视觉口径（docs 88）：中性浅灰底 + 深蓝结构色；状态必须有文字，不只靠颜色；
//   非行动项不用绿色，也不出现「通过/批准/放行」措辞。

import { COUNT_NOTE } from '../lib/todoPlan'
import { subjectDisplayName } from '../lib/materialState'

function CategoryRow({ item, selected, onSelectCategory, onSupplement, subjects = [], materials = [], packets = [] }) {
  const key = item.category || 'unmapped'
  const labelCount = item.labels.length
  const subjectMap = new Map((subjects || []).map((subject) => [String(subject.subject_id), subject]))
  const materialMap = new Map((materials || []).map((material) => [String(material.id ?? material.material_id), material]))
  const packetMap = new Map((packets || []).map((packet) => [String(packet.packet_id), packet]))
  const subjectRefs = item.subjectIds.map((id) => subjectMap.get(String(id))).filter(Boolean)
  const packetRefs = [...new Map(item.materialIds
    .map((id) => materialMap.get(String(id)))
    .filter((material) => material?.packet_id != null)
    .map((material) => [String(material.packet_id), packetMap.get(String(material.packet_id)) || { packet_id: material.packet_id }])).values()]
  return (
    <li
      data-testid={`missing-row-${key}`}
      className={`min-w-0 border-b border-[#E8EEF4] last:border-b-0 ${
        selected ? 'bg-[#EAF2FB]' : ''
      }`}
    >
      <div className="flex min-w-0 items-start gap-2 px-2.5 py-2">
        <button
          type="button"
          data-testid={`missing-category-${key}`}
          aria-pressed={selected}
          onClick={() => onSelectCategory?.(selected ? null : item.category)}
          className="min-w-0 flex-1 text-left"
        >
          <span className={`block truncate text-[12px] font-medium ${selected ? 'text-[#143B63]' : 'text-[#16283D]'}`} title={item.label}>
            {item.label}
          </span>
          <span
            data-testid={`missing-category-count-${key}`}
            className="mt-0.5 block text-[11px] text-[#92400E]"
          >
            补这一份可消 {item.count} 条
          </span>
          {labelCount > 0 && (
            <span className="mt-0.5 block text-[10px] leading-relaxed text-[#64748B]">
              缺：{item.labels.slice(0, 4).join('、')}{labelCount > 4 ? ` 等 ${labelCount} 项` : ''}
            </span>
          )}
          <span className="mt-0.5 block text-[10px] leading-relaxed text-[#64748B]">
            {subjectRefs.length > 0
              ? `主体：${subjectRefs.slice(0, 3).map((subject) => subjectDisplayName(subject) || `主体 ${subject.subject_id}`).join('、')}${subjectRefs.length > 3 ? ` 等 ${subjectRefs.length} 个` : ''}${item.subjectMappingReliable ? '' : '；另有主体待确认'}`
              : '主体归属待确认'}
          </span>
          <span className="mt-0.5 block text-[10px] leading-relaxed text-[#94A3B8]">
            {packetRefs.length > 0
              ? `报审批次：${packetRefs.slice(0, 2).map((packet) => packet?.batch_name || `批次 ${packet?.packet_id}`).join('、')}${packetRefs.length > 2 ? ` 等 ${packetRefs.length} 个` : ''}`
              : '报审批次待确认'}
          </span>
          {item.hint && (
            <span className="mt-0.5 block text-[10px] leading-relaxed text-[#94A3B8]">{item.hint}</span>
          )}
        </button>
        {item.uploadCategory ? (
          <button
            type="button"
            data-testid={`missing-supplement-${key}`}
            onClick={() => onSupplement?.(item)}
            className="mt-0.5 shrink-0 rounded-[4px] border border-[#8B7355] bg-white px-2 py-1 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9]"
          >
            补件
          </button>
        ) : (
          <span className="mt-0.5 shrink-0 rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-1.5 py-0.5 text-[10px] text-[#92400E]">
            无对应上传类别
          </span>
        )}
      </div>
    </li>
  )
}

function ManualRow({ item }) {
  const key = item.category || 'unmapped'
  return (
    <li
      data-testid={`missing-manual-${key}`}
      className="min-w-0 border-b border-[#E8EEF4] px-2.5 py-2 last:border-b-0"
    >
      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="min-w-0 truncate text-[12px] text-[#385A77]" title={item.label}>{item.label}</span>
        <span className="shrink-0 rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-1.5 py-0.5 text-[10px] text-[#92400E]">
          需人工 / 有权口径
        </span>
        <span className="shrink-0 text-[10px] tabular-nums text-[#94A3B8]">{item.count} 条</span>
      </span>
      {item.hint && (
        <span className="mt-0.5 block text-[10px] leading-relaxed text-[#94A3B8]">{item.hint}</span>
      )}
    </li>
  )
}

export default function MissingMaterialPanel({
  plan = null,
  loading = false,
  error = null,
  selectedCategory = null,
  onSelectCategory,
  onSupplement,
  subjects = [],
  materials = [],
  packets = [],
  className = '',
}) {
  const missing = plan?.missing || []
  const manual = plan?.manual || []
  const totals = plan?.totals || null
  const readiness = plan?.readiness || null
  const empty = plan != null && missing.length === 0 && manual.length === 0

  return (
    <section
      data-testid="missing-material-panel"
      aria-label="待补材料"
      className={`flex w-full min-w-0 flex-col rounded-[6px] border border-[#D8E1EB] bg-white ${className}`}
    >
      <header className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-[#E8EEF4] px-2.5 py-1.5">
        <h2 className="text-[13px] font-semibold text-[#16283D]">待补材料</h2>
        {totals && (
          <span data-testid="missing-totals" className="text-[11px] tabular-nums text-[#64748B]">
            待处理共 {totals.undecidable} 条 · 可补件 {totals.itemTotal} 条
          </span>
        )}
      </header>

      {error ? (
        <p data-testid="missing-unavailable" className="px-2.5 py-3 text-[11px] leading-relaxed text-[#92400E]">
          待补材料暂不可用：{error}。可先在上方材料核验台账查看已传材料，稍后重试。
        </p>
      ) : loading || plan == null ? (
        <p data-testid="missing-loading" className="px-2.5 py-3 text-[11px] text-[#94A3B8]">正在读取缺件清单…</p>
      ) : empty ? (
        <p data-testid="missing-empty" className="px-2.5 py-3 text-[11px] leading-relaxed text-[#64748B]">
          本轮没有可补材料的缺口。
        </p>
      ) : (
        <>
          {missing.length > 0 && (
            <>
              <p data-testid="missing-note" className="px-2.5 pt-1.5 text-[10px] leading-relaxed text-[#94A3B8]">
                {COUNT_NOTE}
              </p>
              <ul data-testid="missing-list" className="mt-1 min-w-0">
                {missing.map((item) => (
                  <CategoryRow
                    key={item.category || 'unmapped'}
                    item={item}
                    selected={selectedCategory != null && selectedCategory === item.category}
                    onSelectCategory={onSelectCategory}
                    onSupplement={onSupplement}
                subjects={subjects}
                materials={materials}
                packets={packets}
                  />
                ))}
              </ul>
            </>
          )}

          {manual.length > 0 && (
            <div data-testid="missing-manual-list" className="mt-1.5 border-t border-[#E8EEF4]">
              <p className="px-2.5 pt-1.5 text-[10px] leading-relaxed text-[#94A3B8]">
                非补件缺口（补件补不上，需人工或有权限的口径确认）
              </p>
              <ul className="mt-0.5 min-w-0">
                {manual.map((item) => <ManualRow key={item.category || 'unmapped'} item={item} />)}
              </ul>
            </div>
          )}

          {totals && (totals.openTerms > 0 || totals.withoutMissing > 0) && (
            <div className="space-y-0.5 border-t border-[#E8EEF4] px-2.5 py-1.5">
              {totals.openTerms > 0 && (
                <p data-testid="missing-open-terms" className="text-[10px] leading-relaxed text-[#94A3B8]">
                  另有 {totals.openTerms} 条兜底条款仅登记备查，不计入补件。
                </p>
              )}
              {totals.withoutMissing > 0 && (
                <p data-testid="missing-without-missing" className="text-[10px] leading-relaxed text-[#94A3B8]">
                  另有 {totals.withoutMissing} 条无法核实没有可归类的缺件标签，需人工核实。
                </p>
              )}
            </div>
          )}

          {readiness && readiness.all_completed === false && (
            <p data-testid="missing-readiness" className="border-t border-[#E8EEF4] px-2.5 py-1.5 text-[10px] leading-relaxed text-[#92400E]">
              已选核查组 {(readiness.completed_groups || []).length}/{(readiness.selected_groups || []).length} 完成，
              当前覆盖尚未完整；未完成组：{(readiness.pending_groups || []).join('、') || '—'}。报告仍可先查看，未完成范围会在报告覆盖说明中列出。
            </p>
          )}
        </>
      )}
    </section>
  )
}
