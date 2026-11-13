const SOURCE_STATE_LABELS = {
  absent: '未提供总包合同',
  processing: '总包合同仍在读取',
  unreadable: '总包合同暂不可读取',
  required_pages_unreadable: '规则所需总包页面不可读取',
  unlinked: '总包合同尚未确认归属',
  conflicted: '总包合同存在归属冲突',
}

const STATUS_MESSAGES = {
  running: '范围覆盖核查正在进行',
  incomplete: '范围覆盖核查尚未完成',
  failed: '范围覆盖核查未完成，请重新核查',
  unavailable: '范围覆盖记录暂不可用，请重新核查',
}

export default function ScopeCoverageNotice({ coverage }) {
  if (!coverage || coverage.state === 'legacy_unknown' || coverage.state === 'available') return null

  const limitations = Array.isArray(coverage.limitations) ? coverage.limitations : []
  const statusMessage = STATUS_MESSAGES[coverage.state]
  if (!statusMessage && (!limitations.length || coverage.state !== 'partial')) return null

  return (
    <section
      data-testid="scope-coverage-notice"
      aria-label="规则覆盖范围提示"
      role="status"
      className="col-span-full shrink-0 border border-[#E5C79C] bg-[#FFF8EC] px-3 py-2 text-[12px] text-[#765B36]"
    >
      <h2 className="font-semibold">{statusMessage ? '覆盖状态' : '范围限制'}</h2>
      {statusMessage ? <p className="mt-1 leading-relaxed">{statusMessage}</p> : (
        <>
          <p className="mt-1 leading-relaxed">以下规则本轮未覆盖，未形成符合或不符合结论，也不会进入问题待处理清单。</p>
          <ul className="mt-1 space-y-0.5">
            {limitations.map((item) => (
              <li key={`${item.rule_id}:${item.subject_id ?? 'task'}`} className="break-words">
                <span className="font-medium">{item.rule_id}</span>
                {item.subject_id != null ? ` · 主体 ${item.subject_id}` : ''}
                {` · ${SOURCE_STATE_LABELS[item.source_state] || '来源状态待核实'}`}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
