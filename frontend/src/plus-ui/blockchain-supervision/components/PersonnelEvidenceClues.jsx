import { buildPersonnelClueGroups } from '../lib/personnelClues'

const SIDE_LABELS = { roster: '名单声明', social: '社保', labor: '劳动合同', certificate: '证照字段候选',
  attendance: '考勤记录候选', assignment: '任职记录候选', commitment: '到岗承诺（仅声明）' }

function canOpen(ref, materialsById) {
  const material = materialsById?.[ref.materialId]
  const named = typeof material?.file_name === 'string' && /\.pdf$/i.test(material.file_name.trim())
  return Boolean(named && (material.parse_state == null || material.parse_state === 'done'))
}

export default function PersonnelEvidenceClues({ evidence, materialsById = {}, onOpenSource, findingId }) {
  const groups = buildPersonnelClueGroups(evidence)
  if (!groups.length) return null
  return (
    <section data-testid="personnel-evidence-clues" className="rounded-[6px] border border-[#E5E7EB] bg-[#FCFCFA] px-3 py-2 text-xs text-[#4B5563]">
      <h4 className="font-medium text-[#3B332B]">人员证据线索</h4>
      <p className="mt-1 text-[11px] text-[#9CA3AF]">姓名相同不等于同一人；名单声明与证照字段候选需对照原件核实。</p>
      <ul className="mt-2 space-y-2">
        {groups.map((group) => (
          <li key={group.key} className="break-words">
            <span className="font-medium text-[#3B332B]">{group.name}</span>
            {group.post ? <span>（{group.post}）</span> : null}
            {Object.entries(group.sides).filter(([side]) => group.certificateView
              ? ['roster', 'certificate'].includes(side) : group.attendanceView
                ? ['roster', 'attendance', 'assignment', 'commitment'].includes(side)
                : ['roster', 'social', 'labor'].includes(side)).map(([side, refs]) => (
              refs.length ? (
                <div key={side} className="mt-0.5 pl-2">
                  <span className="text-[#8B7355]">{SIDE_LABELS[side]}：</span>
                  {refs.map((ref, index) => {
                    const material = materialsById?.[ref.materialId]
                    return (
                      <span key={`${ref.materialId}-${ref.page}-${index}`} className="mr-2 inline-block">
                        {material?.file_name || '材料'} · 第 {ref.page} 页
                        {ref.fields?.length ? <span> · {ref.fields.join('、')}</span> : null}
                        {onOpenSource && canOpen(ref, materialsById) ? (
                          <button
                            type="button"
                            className="ml-1 text-[#8B7355] underline"
                            onClick={() => onOpenSource(ref.materialId, ref.page, findingId)}
                          >定位原文</button>
                        ) : null}
                      </span>
                    )
                  })}
                </div>
              ) : side !== 'roster' && group.sides.roster.length ? (
                <div key={side} className="mt-0.5 pl-2 text-[#9CA3AF]">
                  {SIDE_LABELS[side]}：未定位（需人工核对，未定位不代表未提供）
                </div>
              ) : null
            ))}
          </li>
        ))}
      </ul>
    </section>
  )
}
