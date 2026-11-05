// 中栏：当前结论详情（纯只读）。
// 固定顺序（契约 §U1）：结论标识 → 核查说明(message) → 参考依据(source)
//                     → 证据摘要(quote) → 缺失字段/无法核实原因。

function Block({ title, children }) {
  if (!title) return children
  return (
    <section>
      <h4 className="mb-1 text-xs font-semibold tracking-wide text-[#8B7355]">{title}</h4>
      {children}
    </section>
  )
}

function DetailShell({ children, compact = false }) {
  return (
    <section
      aria-label="结论详情"
      data-testid="finding-detail"
      className={`flex min-h-0 w-full min-w-0 flex-col overflow-hidden rounded-[6px] border border-[#EFE6D8] bg-white ${compact ? 'max-h-full' : 'h-full'}`}
    >
      {children}
    </section>
  )
}

/**
 * @param {object}   props
 * @param {object}   [props.finding]   原始 finding DTO（model.selectedFinding）
 * @param {object}   [props.item]      左栏 item（携带派生 subjectLabel / itemId）
 * @param {object[]} [props.evidence]  view-model evidence
 * @param {boolean}  [props.loading]
 * @param {string}   [props.error]     平实错误原因
 */
export default function FindingDetail({ finding, item, evidence = [], materialsById = {}, onOpenSource, loading = false, error = null, compact = false }) {
  if (loading) {
    return (
      <DetailShell compact={compact}>
        <p className="p-6 text-center text-[13px] text-[#9CA3AF]">加载中…</p>
      </DetailShell>
    )
  }
  if (error) {
    return (
      <DetailShell compact={compact}>
        <p data-testid="detail-error" className="p-6 text-center text-[13px] text-[#9CA3AF]">
          {error}
        </p>
      </DetailShell>
    )
  }
  if (!finding) {
    return (
      <DetailShell compact={compact}>
        <p data-testid="detail-empty" className="p-8 text-center text-[13px] text-[#9CA3AF]">
          请从左侧选择一条待办结论
        </p>
      </DetailShell>
    )
  }

  const subjectLabel = item?.subjectLabel
  const source = evidence?.[0] || null
  const locationMode = finding.location_mode ?? source?.locationMode ?? null
  const materialId = finding.material_id ?? source?.materialId ?? null
  const page = finding.page ?? source?.page ?? null
  const material = materialId != null ? materialsById?.[materialId] : null
  const materialExists = material != null
  const isPdf = typeof material?.file_name === 'string' && /\.pdf$/i.test(material.file_name.trim())
  const pageUsable = Number.isInteger(Number(page)) && Number(page) > 0
  const materialReady = material?.parse_state == null || material.parse_state === 'done'
  const canOpenSource = ['precise', 'page'].includes(locationMode) && materialExists &&
    isPdf && pageUsable && materialReady && typeof onOpenSource === 'function'

  return (
    <DetailShell compact={compact}>
      <header className="flex flex-wrap items-center gap-2 border-b border-[#EFE6D8] px-4 py-3">
        {finding.item_id && (
          <span className="rounded border border-[#EFE6D8] bg-[#FDFAF4] px-1.5 py-0.5 text-xs text-[#8B7355]">
            {finding.item_id}
          </span>
        )}
        {subjectLabel && (
          <span className="ml-auto min-w-0 truncate text-xs text-[#6B7280]">归属：{subjectLabel}</span>
        )}
      </header>

      <div className={`min-h-0 flex-1 space-y-3 overflow-y-auto ${compact ? 'p-3' : 'p-4'}`}>
        <Block title={finding.status === 'undecidable' ? '需要人工核对' : '核查说明'}>
          <p
            data-testid={finding.status === 'undecidable' ? 'undecidable-reason' : undefined}
            className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[#1A1A1A]"
          >
            {finding.message}
          </p>
        </Block>

        {finding.source && (
          <Block title="参考依据">
            <p className="break-words text-[13px] leading-relaxed text-[#6B7280]">{finding.source}</p>
          </Block>
        )}

        {(finding.analysis || finding.rule_reason || finding.thinking) && (
          <Block title="分析说明">
            <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-[#6B7280]">
              {finding.analysis || finding.rule_reason || finding.thinking}
            </p>
          </Block>
        )}

        {finding.quote && (
          <Block title="材料摘录">
            <p className="whitespace-pre-wrap break-words rounded-[6px] bg-[#F7F1E9] px-3 py-2 text-[13px] leading-relaxed text-[#1A1A1A]">
              {finding.quote}
            </p>
          </Block>
        )}

        {source?.quote && !finding.quote && (
          <Block title="材料摘录">
            <p className="whitespace-pre-wrap break-words rounded-[6px] bg-[#F7F1E9] px-3 py-2 text-[13px] leading-relaxed text-[#1A1A1A]">{source.quote}</p>
          </Block>
        )}

        {canOpenSource ? (
          <>
            {locationMode === 'page' && <p className="text-xs text-[#9CA3AF]">仅有页码线索：第 {page} 页</p>}
            <button type="button" data-testid="open-source" onClick={() => onOpenSource(materialId, page, finding.id)} className="min-h-9 rounded-[4px] border border-[#8B7355] px-3 py-1.5 text-xs text-[#8B7355] hover:bg-[#FDFAF4]">定位原文</button>
          </>
        ) : locationMode === 'page' && page ? (
          <p className="text-xs text-[#9CA3AF]">仅有页码线索：第 {page} 页</p>
        ) : locationMode === 'unresolved' ? (
          <p className="text-xs text-[#9CA3AF]">暂未定位到具体页</p>
        ) : materialId != null && !materialExists ? (
          <p className="text-xs text-[#9CA3AF]">材料不可用，暂无法定位原文</p>
        ) : null}

      </div>
    </DetailShell>
  )
}
