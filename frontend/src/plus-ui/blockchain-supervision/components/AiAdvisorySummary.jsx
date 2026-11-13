// 仅展示服务端已冻结并完成授权校验的建议，不重新查询或拼接依据。
export default function AiAdvisorySummary({ items, findings = [], coverage = null }) {
  if ((!Array.isArray(items) || !items.length) && !coverage) return null
  const advice = Array.isArray(items) ? items : []
  return <section aria-label="AI 辅助意见" className="rounded border border-[#DCE5EE] bg-white px-4 py-3 text-xs leading-relaxed">
    <h2 className="text-sm font-semibold text-[#587089]">AI 辅助意见</h2>
    <p className="mt-1 text-[#6B7280]">以下为辅助建议，需结合材料和规则由人工核实；不改变系统结论。</p>
    {coverage && <div className="mt-2 text-[#92400E]">
      <p>本次 AI 辅助核查范围（未执行或依据不足的项目需人工核实）：</p>
      {coverage.state === 'unavailable' && <p>核查范围不可用，请重新核查。</p>}
      {(coverage.groups || []).map((group) => <div key={group.group_id} className="mt-1 flex flex-wrap gap-x-3">
        <span className="font-medium">{group.label}：</span>
        {group.state !== 'available' ? <span>状态不可用，请重新核查</span> : <>
          <span className="whitespace-nowrap">完成 {group.counts.completed} 项</span>
          <span className="whitespace-nowrap">未开始 {group.counts.not_started} 项</span>
          <span className="whitespace-nowrap">进行中 {group.counts.pending} 项</span>
          <span className="whitespace-nowrap">部分完成 {group.counts.partial} 项</span>
          <span className="whitespace-nowrap">失败 {group.counts.failed} 项</span>
          <span className="whitespace-nowrap">未执行 {group.counts.not_run} 项</span>
          <span className="whitespace-nowrap">无关联依据 {group.skipped} 项</span>
        </>}
      </div>)}
    </div>}
    <ul className="mt-2 space-y-3">{advice.map((item, index) => {
      const finding = findings.find((row) => String(row.finding_id ?? row.id) === String(item.finding_id))
      const scopeReview = item.proposals?.some((proposal) => proposal.requires_full_scope_review)
      const related = finding?.item_id && finding?.subject_id != null
        ? findings.filter((row) => row.item_id === finding.item_id && row.subject_id === finding.subject_id && row.review?.decision !== 'ignored')
        : finding && finding.review?.decision !== 'ignored' ? [finding] : []
      const messages = [...new Set(related.map((row) => row.message).filter(Boolean))]
      const proposals = item.proposals || []
      const renderProposal = (proposal, n) => <div key={n} className="mt-1 space-y-1">
        <p className="break-words text-[#374151]">{proposal.message}</p>
        {(proposal.material_evidence || []).map((ref, i) => <p key={i} className="break-words text-[#6B7280]">材料第 {ref.page} 页：{ref.quote}</p>)}
        {(proposal.rule_evidence || []).map((ref, i) => <p key={i} className="break-words text-[#6B7280]">依据原文：{ref.quote}</p>)}
      </div>
      return <li key={`${item.finding_id}-${index}`} className="border-t border-[#F3EDE3] pt-2">
      <p className="text-[#6B7280]">{finding?.label || finding?.message || '相关核查项'}</p>
      {scopeReview && messages.filter((message) => message !== finding?.label).slice(0, 3).map((message, n) =>
        <p key={n} className="mt-1 break-words text-[#374151]">规则核查：{message}</p>)}
      {scopeReview && messages.length > 3 && <p className="text-[#6B7280]">其他相关规则结果见核查记录。</p>}
      {item.limitation && <p className="text-[#92400E]">{item.limitation}</p>}
      {proposals.slice(0, 3).map(renderProposal)}
      {proposals.length > 3 && <details className="mt-1 text-[#6B7280]">
        <summary className="cursor-pointer">查看其余 {proposals.length - 3} 条辅助意见</summary>
        {proposals.slice(3).map(renderProposal)}
      </details>}
      {(item.limitations || []).map((limitation, n) => <p key={n} className="mt-1 text-[#92400E]">{limitation}</p>)}
    </li>})}</ul>
  </section>
}
