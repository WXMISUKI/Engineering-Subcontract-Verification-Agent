import { useCallback, useEffect, useRef, useState } from 'react'
import ReviewQueue from './ReviewQueue'
import ReviewActions from './ReviewActions'
import MaterialPreviewPane from './MaterialPreviewPane'
import ReviewCompareMode from './ReviewCompareMode'
import { collectContainerIds } from '../lib/materialState'
import { cleanText } from '../lib/plainText'
import { useMediaQuery } from '../lib/useMediaQuery'
import { primaryEvidenceRef } from '../lib/reviewWorkbenchModel'
import { buildReviewQueueSections, classifyReviewItem } from '../lib/reviewQueueModel'

// 工作台骨架（U1）。
// - 旧版 ≥1280px（Tailwind xl）左侧人工复核、右侧待办/详情/原文预览；<1280px 纵向降级：
//   右栏默认隐藏进抽屉（layout-right-drawer），左栏占满宽度不压缩；断点口径见设计 100 §6。
// - 断点用 matchMedia 判定（useMediaQuery），首帧同步读取避免「先宽屏后切抽屉」闪烁；
//   ≥1280 保持两栏 grid（类名保留，供样式与契约断言）。
// - 所有列加 min-w-0，根加 w-full + min-w-0，杜绝页面级横向溢出。
// - 只做布局与容器：数据全部来自 props（U0 view-model），不含业务逻辑、不请求 API。
// - 非原型路径的问题摘要与人工处置仍合并在左侧；v3 原型路径把详情独立放在中列。

const REASON_CODE_TEXT = {
  field_conflict: '字段冲突',
  field_not_found: '关键字段未提取',
  field_ambiguous: '字段存在歧义',
  post_unmapped: '岗位无证书类别映射',
  subject_unresolved: '主体未确认',
  certificate_not_located: '未定位到证书页',
  certificate_holder_unconfirmed: '证书持证人无法确认',
  certificate_ambiguous: '存在多份互斥证书',
  supplement_unreadable: '补件证书不可读或缺号',
  material_unreadable: '材料质量存疑',
  extraction_failed: '字段提取失败',
}

function reasonCodeText(code) {
  return REASON_CODE_TEXT[code] || code || ''
}

function certificateRoleLabel(role, item = {}) {
  if (role === 'authoritative') return item.generation_id ? '权威补件' : '当前证书'
  if (role === 'superseded') return '已替代'
  return '待确认'
}

function certificateRoleClass(role) {
  if (role === 'authoritative') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (role === 'superseded') return 'border-[#E5E5E5] bg-[#F7F1E9] text-[#6B7280]'
  return 'border-amber-200 bg-amber-50 text-amber-700'
}

const EVIDENCE_ROLE_TEXT = {
  support: '支持证据',
  contradiction: '矛盾证据',
  expected: '名单/要求证据',
  unreadable: '不可读证据',
  authoritative: '权威补件',
  superseded: '已替代证据',
  candidate: '候选证据',
}

function evidenceRoleLabel(role) {
  return EVIDENCE_ROLE_TEXT[role] || (role ? `证据角色：${role}` : '')
}

function evidenceRoleClass(role) {
  if (role === 'contradiction') return 'border-red-200 bg-red-50 text-red-700'
  if (role === 'support') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (role === 'expected') return 'border-sky-200 bg-sky-50 text-sky-700'
  if (role === 'unreadable') return 'border-amber-200 bg-amber-50 text-amber-700'
  return 'border-[#E5DED3] bg-[#FCFAF6] text-[#705C45]'
}

function quoteHeading(finding) {
  if (finding?.anchor?.status === 'page_only') return '材料线索'
  return (finding?.location_mode ?? finding?.locationMode) === 'precise' ? '匹配原文' : '材料线索'
}

function sourceLocation({ finding, material, containerIds }) {
  const evidenceRef = primaryEvidenceRef(finding)
  const anchor = finding?.anchor ?? evidenceRef?.anchor
  const anchorMode = anchor?.status === 'precise' && anchor?.coordinate_space === 'pdf_page'
    ? 'precise'
    : anchor?.status === 'page_only'
      ? 'page'
      : null
  const locationMode = finding?.location_mode ?? finding?.locationMode
    ?? evidenceRef?.location_mode ?? evidenceRef?.locationMode ?? anchorMode
  const page = Number(finding?.page ?? evidenceRef?.page)
  const hasPage = Number.isInteger(page) && page > 0
  const materialId = material?.id ?? material?.material_id
    ?? finding?.material_id ?? finding?.materialId ?? evidenceRef?.material_id ?? evidenceRef?.materialId
  const fileName = typeof material?.file_name === 'string' ? material.file_name.trim() : ''
  const isPdf = /\.pdf$/i.test(fileName)

  if (locationMode === 'precise') {
    if (!material) return { label: '材料不可用，暂无法定位原文' }
    if (containerIds.has(String(materialId))) return { label: '该材料为 ZIP 容器，暂不支持原文预览' }
    if (material.parse_state !== 'done') return { label: '材料尚未解析完成，暂无法定位原文' }
    if (!isPdf) return { label: '该材料暂不支持原文预览' }
    if (!hasPage) return { label: '暂未定位到具体页' }
    return { label: `已定位到第 ${page} 页`, canOpen: true, materialId, page }
  }
  if (locationMode === 'page' && hasPage) {
    if (material && material.parse_state === 'done' && isPdf && !containerIds.has(String(materialId))) {
      return { label: `仅有页码线索：第 ${page} 页`, canOpen: true, materialId, page }
    }
    return { label: `仅有页码线索：第 ${page} 页` }
  }
  if (locationMode === 'unresolved') return { label: '暂未定位到具体页' }
  return { label: '暂无可用的原文定位信息' }
}

function publicationPolicyMessage(publication) {
  if (!publication || typeof publication !== 'object') return null
  const state = publication.state
  const policy = publication.reviewPolicy
  const generation = (state === 'candidate' || policy === 'candidate_requires_review')
    ? (publication.candidateGenerationId || publication.generationId || '未生成')
    : (publication.generationId || '未生成')
  const baseline = publication.baselineVersionNo == null ? '未记录' : publication.baselineVersionNo
  if (state === 'candidate' || policy === 'candidate_requires_review') {
    return {
      tone: 'candidate',
      title: '当前为补件候选代次，尚未发布',
      detail: `候选代次：${generation}；基线复核版本：${baseline}。原人工意见不会自动继承，需在当前候选代次重新复核。`,
    }
  }
  if (state === 'failed' || policy === 'baseline_only') {
    return {
      tone: 'failed',
      title: '补件候选运行未发布，当前仍以基线代次为准',
      detail: `当前公开代次：${generation}；基线复核版本：${baseline}。候选运行失败或被中断，人工意见与报告不会写入失败候选。`,
    }
  }
  return null
}

function trapTabFocus(event, container) {
  if (event.key !== 'Tab' || !container) return
  const focusables = Array.from(container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
    .filter((node) => {
      if (node.disabled || node.getAttribute('aria-hidden') === 'true') return false
      if (node.hidden || node.closest('[hidden], [aria-hidden="true"]')) return false
      return true
    })
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function FindingDetailPane({
  finding,
  materialsById,
  containerIds,
  onOpenSource,
  compact = false,
  prototypeMode = false,
  actions = {},
  submitting = false,
  onReview,
  onNext,
  onCompare,
  compareTriggerRef,
}) {
  if (!finding) return null
  const material = finding.material_id != null ? materialsById?.[finding.material_id] : null
  const location = sourceLocation({ finding, material, containerIds })
  const findingId = finding.id ?? finding.findingId
  const source = cleanText(finding.source) || ''
  // 刀D（设计 104 §4.2）：消息/摘录只做展示层消毒，库中原文不动。
  const quote = cleanText(finding.quote) || ''
  const message = cleanText(finding.message) || cleanText(finding.title) || '未提供问题描述'
  const reasonCode = finding.reasonCode || finding.reason_code ||
    (Array.isArray(finding.evidence)
      ? (finding.evidence.find((ref) => ref && ref.reason_code)?.reason_code ?? null)
      : null)
  const reasonLabel = finding.reasonLabel || finding.reason_label || reasonCodeText(reasonCode)
  const readingPages = finding.status === 'undecidable' && finding.subject_id != null && message.includes('未读')
    ? Object.values(materialsById || {}).flatMap((row) => {
      const id = row?.id ?? row?.material_id
      const fileName = row?.file_name
      const total = row?.reading_coverage?.total_pages
      if (id == null || row?.subject_id == null || String(row.subject_id) !== String(finding.subject_id) ||
          typeof fileName !== 'string' || !message.includes(`「${fileName}」`) || row.superseded_by_id != null) return []
      const sameName = Object.values(materialsById || {}).filter((other) =>
        other?.subject_id != null && String(other.subject_id) === String(finding.subject_id) && other.file_name === fileName)
      if (sameName.length !== 1) return []
      const pages = [row.reading_coverage?.unread_pages, row.reading_coverage?.suspect_pages]
        .flatMap((list) => Array.isArray(list) ? list : [])
      return [...new Set(pages)].filter((page) => Number.isInteger(page) && page > 0 &&
        (!Number.isInteger(total) || page <= total)).sort((a, b) => a - b).flatMap((page) => {
        const location = sourceLocation({ finding: { material_id: id, page, location_mode: 'page' }, material: row, containerIds })
        return location.canOpen ? [{ id, fileName, page }] : []
      })
    }) : []
  const supplementIssues = Object.values(materialsById || {}).flatMap((row) => {
    const link = row?.supplement_issue
    const linkedItems = Array.isArray(link?.item_ids) ? link.item_ids : [link?.item_id]
    if (!link || finding.item_id == null || finding.subject_id == null || row.superseded_by_id != null ||
        !linkedItems.some((itemId) => String(itemId) === String(finding.item_id)) || String(link.subject_id) !== String(finding.subject_id)) return []
    const issue = (row.degradations || []).find((entry) => ['subject_conflict', 'subject_unresolved'].includes(entry?.kind))
    return issue ? [{ material: row, issue }] : []
  })
  const basis = finding.basis && typeof finding.basis === 'object' ? finding.basis : null
  const basisText = basis ? cleanText(basis.text) : null
  const basisDoc = basis ? (cleanText(basis.doc_no) || cleanText(basis.doc_title) || basis.doc_id || '依据库') : null
  const basisMarker = basis ? cleanText(basis.marker) : null
  const basisVerified = basis?.human_verified
  const prototypeReviewLabel = finding.review?.decision === 'accepted'
    ? '已采纳'
    : finding.review?.decision === 'ignored'
      ? '已忽略'
      : '未经人工处理'
  // 刀B（设计 98 §4.4）：结论变化后旧回执被作废。这里只做留痕展示：
  // 不提供「恢复旧意见」按钮（重新处置走复核表单），并与「未复核」徽标区分。
  const invalidated = finding.review?.invalidated || null
  const previousReview = invalidated?.previous || null
  const noMatchText = quote
    ? null
    : finding.status === 'undecidable'
      ? '本条没有匹配原文——未取得可核实的材料证据，需补件或人工核实'
      : finding.status === 'miss'
        ? '符合项无引用原文（按规则判定）'
        : '本条未记录匹配原文，仅有规则依据与问题描述'

  return (
    <section data-testid="finding-detail-pane" aria-label="问题详情" className={`flex ${compact ? 'min-h-0' : 'min-h-[360px]'} min-w-0 flex-1 flex-col overflow-y-auto rounded-[6px] border border-[#EFE6D8] bg-white`}>
      <header className={prototypeMode ? 'sr-only' : 'shrink-0 border-b border-[#EFE6D8] px-4 py-3'}>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-[#8B7355]">问题详情</h2>
          {!prototypeMode && finding.item_id && <span className="rounded border border-[#EFE6D8] px-1.5 py-0.5 text-[10px] text-[#8B7355]">{finding.item_id}</span>}
          {invalidated && (
            <span data-testid="detail-invalidated" className="rounded border border-[#F0DCB8] bg-[#FFFDF8] px-1.5 py-0.5 text-[10px] text-[#92400E]">原复核意见失效</span>
          )}
        </div>
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-[13px] leading-relaxed text-[#374151]">
        {supplementIssues.map(({ material: row, issue }) => (
          <p key={row.id} role="alert" className="rounded border border-[#F0DCB8] bg-[#FFF9ED] px-3 py-2 text-[#92400E]">
            {issue.kind === 'subject_conflict'
              ? `补件未通过：${row.file_name}的材料主体为${issue.detected_subject_name || '待确认'}，与当前补件主体${issue.expected_subject_name || '待确认'}不一致，请返回材料页重新上传。`
              : `补件主体无法确认：${row.file_name}；请返回材料页核对或重新上传。`}
          </p>
        ))}
        {prototypeMode && (
          <section data-testid="prototype-finding-heading" className="mb-1 border-b border-[#DCE5EE] pb-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={`rounded-full border px-2 py-0.5 text-[10px] leading-none ${prototypeReviewLabel === '未经人工处理' ? 'border-[#EAD5AA] bg-[#FFF8E9] text-[#96651C]' : 'border-[#C8E4CF] bg-[#EFFAF2] text-[#2F7D49]'}`}>{prototypeReviewLabel}</span>
            </div>
            <h2 className="mt-3 text-[19px] font-semibold leading-snug text-[#17283D]">{message}</h2>
            <p className="mt-1 text-[12px] leading-relaxed text-[#587089]">{source || finding.status_label || '系统依据已发现一项需要人工查看的问题'}</p>
          </section>
        )}
        <section className={prototypeMode ? 'sr-only' : undefined}>
          <h3 className="mb-1 text-xs font-medium text-[#6B7280]">问题描述</h3>
          <p data-testid="detail-problem-description" className="break-words text-[#1F2937]">{message}</p>
        </section>
        {reasonCode && (
          <section data-testid="detail-reason-code" className={prototypeMode ? 'sr-only' : undefined}>
            <h3 className="mb-1 text-xs font-medium text-[#6B7280]">字段原因</h3>
            <p className="break-words rounded-[4px] border border-[#EFE6D8] bg-[#FDFAF4] px-3 py-2 text-[12px] text-[#705C45]">
              {reasonLabel}
            </p>
          </section>
        )}
        {readingPages.length > 0 && onOpenSource && (
          <details className="rounded-[4px] border border-[#EFE6D8] px-3 py-2 text-xs text-[#587089]">
            <summary className="cursor-pointer font-medium">材料待读页（{readingPages.length} 页）</summary>
            <p className="mt-2">这些页面尚未可靠读取，是否与本问题相关请结合原件核实。</p>
            <div className="mt-1 flex flex-wrap gap-2">
              {readingPages.map(({ id, fileName, page }) => (
                <button key={`${id}-${page}`} type="button" className="min-h-8 rounded border border-[#C8B89E] px-2 text-[#8B7355] hover:bg-[#FDFAF4]"
                  aria-label={`${fileName} 第 ${page} 页（待读）`} onClick={() => onOpenSource(id, page, findingId)}>第 {page} 页</button>
              ))}
            </div>
          </details>
        )}
        {invalidated && (
          <section data-testid="invalidated-review-trace" className="rounded-[4px] border border-[#F0DCB8] bg-[#FFFDF8] px-3 py-2 text-[12px] leading-relaxed text-[#92400E]">
            <h3 className="font-medium">原复核意见失效</h3>
            <p className="mt-0.5">结论变化后原复核意见已失效，需重新处置；旧意见仅作留痕。</p>
            {previousReview && (
              <p data-testid="invalidated-review-previous" className="mt-1 break-words text-[#6B7280]">
                原处置：{previousReview.decision_label || previousReview.decision || '—'}
                {previousReview.note ? ` · 原意见：${previousReview.note}` : ''}
                {previousReview.reviewer ? ` · ${previousReview.reviewer}` : ''}
                {previousReview.at ? ` · ${String(previousReview.at).replace('T', ' ')}` : ''}
              </p>
            )}
          </section>
        )}
        {/* 刀D（设计 104 §4.1）：先规则后材料——“触犯哪条规则”要能直接读到条款原文。 */}
        {basis ? (
          <section data-testid="detail-basis" className="rounded-[4px] border border-[#EFE6D8] bg-[#FCFAF6] px-3 py-2">
            <h3 className="mb-1 text-xs font-medium text-[#6B7280]">规则依据</h3>
            <p className="break-words text-[12px] font-medium text-[#4B5563]">
              {basisDoc}{basisMarker ? ` · ${basisMarker}` : ''}
              {Number.isInteger(basis.page_start) ? `（依据库第 ${basis.page_start}${Number.isInteger(basis.page_end) && basis.page_end !== basis.page_start ? `–${basis.page_end}` : ''} 页）` : ''}
            </p>
            {basisText ? (
              <blockquote data-testid="detail-basis-text" className="mt-1 break-words border-l-2 border-[#C8B89E] pl-2 text-[12px] leading-relaxed text-[#4B5563]">
                {basisText}
              </blockquote>
            ) : (
              <p className="mt-1 text-[12px] text-[#9CA3AF]">依据库未收录该条款原文。</p>
            )}
            <p data-testid="detail-basis-verification" className="mt-1 text-[11px] text-[#9CA3AF]">
              {basisVerified === true ? '依据原文已人工校对' : basisVerified === false ? '依据原文未人工校对（OCR），引用前需核对原件' : '依据库未记录校对状态'}
            </p>
          </section>
        ) : (
          <p data-testid="detail-basis-absent" className="rounded-[4px] border border-dashed border-[#E5E7EB] px-3 py-2 text-[12px] text-[#9CA3AF]">
            该条目未建立条款坐标（依据库未收录），以下“参考依据”为条目出处。
          </p>
        )}
        {source && (
          <section className={prototypeMode ? 'sr-only' : undefined}>
            <h3 className="mb-1 text-xs font-medium text-[#6B7280]">参考依据</h3>
            <p data-testid="detail-reference" className="break-words">{source}</p>
          </section>
        )}
        {quote ? (
          <section>
            <h3 className="mb-1 text-xs font-medium text-[#6B7280]">{quoteHeading(finding)}{!prototypeMode && quoteHeading(finding) === '匹配原文' ? '（材料摘录）' : ''}</h3>
            <blockquote data-testid="detail-quote" className="break-words border-l-2 border-[#C8B89E] bg-[#FAF8F4] px-3 py-2 text-[#4B5563]">{quote}</blockquote>
            <p className="mt-1 text-[11px] text-[#9CA3AF]">
              {material?.file_name ? `材料：${material.file_name}` : '材料不可用'}
              {Number.isInteger(Number(finding.page)) && Number(finding.page) > 0 ? ` · 第 ${Number(finding.page)} 页` : ''}
            </p>
          </section>
        ) : (
          <section>
            <h3 className="mb-1 text-xs font-medium text-[#6B7280]">匹配原文</h3>
            <p data-testid="detail-no-match" className={`break-words rounded-[4px] px-3 py-2 text-[12px] ${prototypeMode ? 'border-l-2 border-[#B79052] bg-[#FBF8F2] text-[#587089]' : 'border border-dashed border-[#E5E7EB] text-[#9CA3AF]'}`}>{noMatchText}</p>
          </section>
        )}
        {(finding.evidence || []).map((ref, index) => {
          const refMaterial = materialsById?.[ref.material_id]
          const loc = sourceLocation({
            finding: {
              ...finding,
              ...ref,
              location_mode: ref.location_mode ?? ref.locationMode ?? finding.location_mode ?? finding.locationMode,
              anchor: ref.anchor,
            },
            material: refMaterial,
            containerIds,
          })
          const refQuote = cleanText(ref.quote)
          return <section key={index} className={prototypeMode ? 'sr-only' : 'rounded border border-[#EFE6D8] p-3'}>
            <h3 className="text-xs font-medium">{refMaterial?.file_name || '材料不可用'}</h3>
            {refQuote && <blockquote className="my-2 break-words text-xs text-[#6B7280]">{refQuote}</blockquote>}
            {loc.canOpen ? <button type="button" className="min-h-8 text-xs text-[#8B7355] hover:underline" onClick={() => onOpenSource?.(loc.materialId, loc.page, findingId)}>定位原文 · 第 {loc.page} 页</button> : <p className="text-xs text-[#9CA3AF]">{loc.label}</p>}
            {Array.isArray(ref.certificate_evidence) && ref.certificate_evidence.length > 0 && (
              <div data-testid={`certificate-evidence-${index}`} className="mt-3 rounded-[4px] border border-[#EFE6D8] bg-[#FDFAF4] px-2 py-2">
                <p className="mb-1 text-[11px] font-medium text-[#8B7355]">补件证据对照</p>
                <ul className="space-y-1">
                  {ref.certificate_evidence.map((item, itemIndex) => {
                    const itemMaterial = materialsById?.[item.material_id]
                    return (
                      <li key={`${item.material_id}-${item.page}-${itemIndex}`} data-testid={`certificate-evidence-item-${index}-${itemIndex}`} className="flex flex-wrap items-center gap-1 text-[11px] leading-relaxed text-[#4B5563]">
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] leading-none ${certificateRoleClass(item.role)}`}>
                          {certificateRoleLabel(item.role, item)}
                        </span>
                        <span>{itemMaterial?.file_name || '材料不可用'}</span>
                        {item.page ? <span>· 第 {item.page} 页</span> : null}
                        {item.certificate_number ? <span>· {item.certificate_number}</span> : null}
                        {item.quality && item.quality !== 'readable' ? <span>· 质量待核实</span> : null}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </section>
        })}
        <section data-testid="detail-location-status" className={prototypeMode ? 'sr-only' : undefined}>
          <h3 className="mb-1 text-xs font-medium text-[#6B7280]">原文定位</h3>
          <p className="break-words text-[#6B7280]">{location.label}</p>
          {location.canOpen && onOpenSource && (
            <button
              type="button"
              data-testid="detail-open-source"
              onClick={() => onOpenSource(location.materialId, location.page, findingId)}
              className="mt-2 inline-flex min-h-8 items-center rounded-[4px] border border-[#C8B89E] px-3 py-1.5 text-xs text-[#8B7355] hover:bg-[#FDFAF4]"
            >
              定位原文 · 第 {location.page} 页
            </button>
          )}
        </section>
        {!compact && (
          <p data-testid="material-preview-empty" className={`border-t border-[#F1ECE4] pt-3 text-[12px] text-[#9CA3AF] ${prototypeMode ? 'sr-only' : ''}`}>
            点击问题中的“定位原文”，这里会显示对应材料原文
          </p>
        )}
      </div>
      {prototypeMode && (
        <div data-testid="prototype-detail-actions" className="shrink-0 border-t border-[#EFE6D8] p-3">
          <h3 className="mb-2 text-xs font-medium text-[#6B7280]">人工说明（选填）</h3>
          <ReviewActions
            compact
            finding={{
              ...finding,
              id: findingId,
              message,
              status_label: finding.statusLabel,
            }}
            item={finding}
            evidence={quote ? [{ quote }] : []}
            actions={actions}
            submitting={submitting}
            onReview={onReview}
            persistentActions={
              <>
                <button type="button" disabled={!location.canOpen || !onOpenSource} onClick={() => onOpenSource?.(location.materialId, location.page, findingId)} className="min-h-10 rounded-[4px] border border-[#8B7355] px-3 py-1.5 text-xs text-[#8B7355] hover:bg-[#F7F1E9] disabled:cursor-not-allowed disabled:opacity-50">定位原文</button>
                <button ref={compareTriggerRef} type="button" onClick={onCompare} className="min-h-10 rounded-[4px] border border-[#8B7355] px-3 py-1.5 text-xs text-[#8B7355] hover:bg-[#F7F1E9]">全屏对照</button>
                <span className="flex-1" />
              </>
            }
          />
        </div>
      )}
    </section>
  )
}

function CompareQuickActions({
  finding,
  actions,
  submitting,
  onReview,
  onNext,
  materialsById = {},
  containerIds = new Set(),
  onOpenSource,
}) {
  if (!finding) return null
  const basis = finding.basis && typeof finding.basis === 'object' ? finding.basis : null
  const basisText = basis?.text || finding.basisText || finding.source || '依据库未提供条款原文。'
  const basisDoc = basis?.doc_no || basis?.doc_title || basis?.doc_id || '规则依据'
  const reasonCode = finding.reasonCode || finding.reason_code ||
    (Array.isArray(finding.evidence)
      ? (finding.evidence.find((ref) => ref && ref.reason_code)?.reason_code ?? null)
      : null)
  const reasonLabel = finding.reasonLabel || finding.reason_label || reasonCodeText(reasonCode)
  const subjectLabel = finding.subjectLabel || finding.subject_label || finding.subject_name || null
  const decisionLabel = finding.review?.decision === 'accepted'
    ? '已采纳'
    : finding.review?.decision === 'ignored'
      ? '已忽略'
      : '未经人工处理'
  const problemDescription = finding.description || finding.problem_description || finding.analysis || finding.rule_reason || finding.message || '未提供详细问题描述。'
  const reasoning = finding.analysis || finding.reasoning || finding.rule_reason || finding.explanation || null
  const decisionTrace = finding.decision_trace || finding.decisionTrace || null
  const aiAdjudication = finding.ai_adjudication || finding.aiAdjudication || null
  const aiAdjudicationLabel = { hit: '不符合', miss: '符合', undecidable: '无法核实' }[aiAdjudication?.status] || null
  const evidenceStatusLabel = {
    precise: '已取得可验证页码和引文',
    page_only: '只有页码，尚未完成引文定位校验',
    material_without_location: '已关联材料，但没有可验证页码或引文',
    missing: '没有绑定可核实的材料证据',
    rule_only: '本条按已落库规则结果展示，没有单页材料引文',
  }[decisionTrace?.evidence_status] || '未提供材料证据状态'
  const missingLabels = Array.isArray(decisionTrace?.missing?.labels)
    ? decisionTrace.missing.labels.filter(Boolean)
    : []
  const evidenceRows = Array.isArray(finding.evidence) ? finding.evidence : []
  const primaryMaterialId = finding.material_id ?? finding.materialId
  const primaryMaterial = primaryMaterialId != null
    ? materialsById[primaryMaterialId] || materialsById[String(primaryMaterialId)] || null
    : null
  const primaryLocation = sourceLocation({ finding, material: primaryMaterial, containerIds })
  const primaryEvidence = evidenceRows.find((reference) =>
    String(reference.material_id ?? reference.materialId) === String(primaryLocation.materialId)
    && Number(reference.page) === Number(primaryLocation.page))
  const additionalEvidence = evidenceRows.filter((reference) => !primaryLocation.canOpen
    || String(reference.material_id ?? reference.materialId) !== String(primaryLocation.materialId)
    || Number(reference.page) !== Number(primaryLocation.page))
  const handleReview = async (...args) => {
    const saved = await onReview?.(...args)
    if (saved !== false) onNext?.()
  }

  return (
    <aside data-testid="compare-quick-actions" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-white p-5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded-full border px-2 py-0.5 text-[10px] ${decisionLabel === '未经人工处理' ? 'border-[#EAD5AA] bg-[#FFF8E9] text-[#96651C]' : 'border-[#C8E4CF] bg-[#EFFAF2] text-[#2F7D49]'}`}>{decisionLabel}</span>
        {subjectLabel && <span data-testid="compare-subject" className="text-[11px] text-[#6B7280]">归属：{subjectLabel}</span>}
      </div>
      <h2 className="mt-2 text-sm font-semibold leading-relaxed text-[#17283D]">{finding.message || finding.title || '当前问题'}</h2>
      <section data-testid="compare-problem-description" className="mt-4">
        <h3 className="mb-1 text-xs font-medium text-[#7A6C5A]">问题描述</h3>
        <p className="text-[13px] leading-7 text-[#4B4035]">{problemDescription}</p>
      </section>
      {reasonCode && (
        <section data-testid="compare-reason-code" className="mt-3 rounded-[4px] border border-[#EFE6D8] bg-[#FDFAF4] px-3 py-2">
          <h3 className="text-[11px] font-medium text-[#7A6C5A]">字段原因</h3>
          <p className="mt-1 text-[12px] text-[#705C45]">{reasonLabel}</p>
        </section>
      )}
      <section data-testid="compare-ai-reasoning" className="mt-3 rounded-[4px] border border-[#DCE5EE] bg-[#F7F9FB] px-3 py-2">
        <details>
          <summary className="cursor-pointer text-[11px] font-medium text-[#587089]">判定链（基于已落库数据）</summary>
          <ol className="mt-1 list-decimal space-y-1 pl-4 text-[11px] leading-relaxed text-[#4B5563]">
            <li>结论状态：{decisionTrace?.status_label || finding.status_label || finding.status || '未提供'}</li>
            <li>规则依据：{decisionTrace?.basis_available ? '已建立规则依据坐标' : '未建立规则依据坐标'}</li>
            <li>材料证据：{evidenceStatusLabel}</li>
            <li>待核缺口：{missingLabels.length ? missingLabels.join('、') : '未记录结构化缺口'}</li>
          </ol>
          <p className="mt-2 text-[10px] leading-relaxed text-[#7A6C5A]">
            {reasoning || '后端未提供独立的模型思维过程；以上内容仅展示可审计的状态、规则依据、材料证据和缺口。'}
          </p>
        </details>
      </section>
      {aiAdjudication && aiAdjudicationLabel && (
        <section data-testid="compare-ai-adjudication" className="mt-3 rounded-[4px] border border-[#DCE5EE] bg-[#F7F9FB] px-3 py-2">
          <h3 className="text-[11px] font-medium text-[#587089]">AI 判定</h3>
          <p className="mt-1 text-[12px] leading-relaxed text-[#4B5563]">
            AI 认定：<b>{aiAdjudicationLabel}</b>
            {aiAdjudication.reason ? `；理由：${aiAdjudication.reason}` : ''}
            {aiAdjudication.model_code ? `；模型：${aiAdjudication.model_code}` : ''}
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-[#7A6C5A]">该判定已写入机器结论审计；人工采纳/忽略仍作为独立回执保留。</p>
        </section>
      )}
      <section className="mt-3 rounded-[4px] border border-[#EFE6D8] bg-[#FCFAF6] px-3 py-2">
        <h3 className="text-[11px] font-medium text-[#6B7280]">规则依据</h3>
        <p className="mt-1 text-[11px] font-medium leading-relaxed text-[#4B5563]">{basisDoc}</p>
        <p className="mt-1 text-[11px] leading-relaxed text-[#4B5563]">{basisText}</p>
      </section>
      {finding.quote && (
        <section data-testid="compare-matched-quote" className="mt-3 rounded-[4px] border-l-2 border-[#B79052] bg-[#FBF8F2] px-3 py-2">
          <h3 className="text-[11px] font-medium text-[#6B7280]">{quoteHeading(finding)}</h3>
          <blockquote className="mt-1 text-[12px] leading-relaxed text-[#4B5563]">{cleanText(finding.quote)}</blockquote>
        </section>
      )}
      <section data-testid="compare-evidence-location" className="mt-3 rounded-[4px] border border-[#E5DED3] bg-white px-3 py-2">
        <h3 className="text-[11px] font-medium text-[#7A6C5A]">证据定位</h3>
        {primaryLocation.canOpen && onOpenSource ? (
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-[#4B5563]">
            {primaryEvidence?.role && (
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${evidenceRoleClass(primaryEvidence.role)}`}>
                {evidenceRoleLabel(primaryEvidence.role)}
              </span>
            )}
            <span>{primaryMaterial?.file_name || '当前材料'} · 第 {primaryLocation.page} 页</span>
            <button
              type="button"
              data-testid="compare-open-primary-source"
              onClick={() => onOpenSource(primaryLocation.materialId, primaryLocation.page, finding.id ?? finding.findingId)}
              className="min-h-8 rounded-[4px] border border-[#8B7355] px-2 py-1 text-[11px] text-[#8B7355] hover:bg-[#FBF5EC]"
            >
              定位原文
            </button>
          </div>
        ) : null}
        {additionalEvidence.length > 0 ? (
          <ul className="mt-1 space-y-1">
            {additionalEvidence.map((reference, index) => {
              const materialId = reference.material_id ?? reference.materialId
              const material = materialsById[materialId] || materialsById[String(materialId)] || null
              const location = sourceLocation({ finding: reference, material, containerIds })
              return (
                <li key={`${reference.material_id || 'evidence'}-${reference.page || index}`} className="flex flex-wrap items-center gap-2 text-[11px] text-[#4B5563]">
                  {reference.role && <span className={`rounded border px-1.5 py-0.5 text-[10px] ${evidenceRoleClass(reference.role)}`}>{evidenceRoleLabel(reference.role)}</span>}
                  <span>{material?.file_name || '材料证据'} · {location.label}</span>
                  {location.canOpen && onOpenSource && (
                    <button
                      type="button"
                      data-testid={`compare-open-evidence-${index}`}
                      onClick={() => onOpenSource(location.materialId, location.page, finding.id ?? finding.findingId)}
                      className="min-h-8 rounded-[4px] border border-[#8B7355] px-2 py-1 text-[11px] text-[#8B7355] hover:bg-[#FBF5EC]"
                    >
                      定位原文 · 第 {location.page} 页
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        ) : !(primaryLocation.canOpen && onOpenSource) ? (
          <p data-testid="compare-evidence-location-empty" className="mt-1 text-[11px] leading-relaxed text-[#9CA3AF]">
            当前问题没有可用的材料证据定位，不能虚构页码；请依据规则依据和人工核查意见完成处置。
          </p>
        ) : null}
      </section>
      <div data-testid="compare-review-actions" className="mt-5 pt-4">
        <ReviewActions
          compact
          finding={finding}
          item={finding}
          evidence={finding.quote ? [{ quote: finding.quote }] : []}
          actions={actions}
          submitting={submitting}
          onReview={handleReview}
        />
      </div>
    </aside>
  )
}

/**
 * @param {object}   props
 * @param {object}   [props.model]          U0 view-model（groups/selectedFindingId/selectedFinding/evidence/actions）
 * @param {boolean}  [props.loading]
 * @param {string}   [props.error]          平实错误原因
 * @param {boolean}  [props.missCollapsed]
 * @param {Function} [props.onToggleMiss]
 * @param {Function} [props.onSelect]       (findingId) => void
 * @param {Function} [props.onReview]       (findingId, decision, note) => void
 * @param {Function} [props.onError]       (message) => void
 * @param {boolean}  [props.submitting]     复核提交中（透传右栏，禁用重复提交）
 * @param {Function} [props.onComplete]     可选：完成人工复核并生成报告
 * @param {boolean}  [props.completing]     报告生成中
 * @param {boolean}  [props.completeDisabled] 报告前置条件未满足
 * @param {string}   [props.className]
 */
export default function ReviewWorkbenchLayout({
  model,
  loading = false,
  error = null,
  readingCoverage = null,
  missCollapsed = true,
  onToggleMiss,
  onSelect,
  onSelectAndOpenSource,
  onReview,
  onReturnOverview,
  onError,
  submitting = false,
  onComplete,
  completing = false,
  completeDisabled = false,
  completeLabel = '生成报告',
  previewMaterialId = null,
  previewPage = null,
  previewFindingId = null,
  previewClosed = true,
  previewContainerIds = [],
  onPreviewMaterialChange,
  onClosePreview,
  onOpenSource,
  onSupplement,
  className = '',
  reviewFilter,
  onFilterChange,
    publication = null,
  // V2：右栏顶部「待办与处置」分组（可选；不传时右栏保持原样，历史调用不受影响）。
  todoPanel = null,
  stepPanel = null,
  overviewPanel = null,
  overviewMode = false,
  // V2：筛选后无结论时的左栏空态文案（可选；不传时沿用原空态）。
  emptyLabel = null,
  // v3 原型：宽桌面为左待办/中详情/右问题队列；较窄视口纵向堆叠三列内容。
  prototypeMode = false,
}) {
  const groups = model?.groups || []
  const selectedFindingId = model?.selectedFindingId ?? null
  const selectedFinding = model?.selectedFinding || null
  const allMaterials = Object.values(model?.materialsById || {})
  const storedContainerIds = allMaterials
    .filter((material) => material?.parse_state === 'stored')
    .map((material) => material.id ?? material.material_id)
  const containerIds = new Set(
    [...collectContainerIds(allMaterials, [...storedContainerIds, ...previewContainerIds])].map((id) => String(id)),
  )
  const previewMaterials = allMaterials.filter((material) => {
    const id = material.id ?? material.material_id
    const fileName = typeof material.file_name === 'string' ? material.file_name.trim() : ''
    return material.parse_state === 'done' && /\.pdf$/i.test(fileName) && !containerIds.has(String(id))
  })
  const previewMaterialsById = Object.fromEntries(
    previewMaterials.map((material) => [material.id ?? material.material_id, material]),
  )
  const basePreviewFinding = previewFindingId == null
    ? null
    : (selectedFinding && String(selectedFinding.id ?? selectedFinding.findingId) === String(previewFindingId)
      ? selectedFinding
      : groups.flatMap((group) => group.items || []).find((item) => String(item.findingId) === String(previewFindingId)) || null)
  const reference = basePreviewFinding?.evidence?.find((ref) => String(ref.material_id) === String(previewMaterialId) && Number(ref.page) === Number(previewPage))
  const samePrimary = basePreviewFinding && String(basePreviewFinding.materialId ?? basePreviewFinding.material_id) === String(previewMaterialId)
    && Number(basePreviewFinding.page) === Number(previewPage)
  const previewFinding = reference ? { ...basePreviewFinding, ...reference, materialId: reference.material_id }
    : basePreviewFinding && !samePrimary ? { ...basePreviewFinding, materialId: previewMaterialId,
      material_id: previewMaterialId, page: previewPage, quote: null, anchor: null, location_mode: 'page', evidence: [] }
      : basePreviewFinding
  const previewMaterial = !previewClosed && previewMaterialId != null
    ? previewMaterialsById[previewMaterialId] || previewMaterialsById[String(previewMaterialId)] || null
    : null
  const sameSourceMaterial = Boolean(
    previewMaterial && previewFinding &&
    Number.isFinite(Number(previewPage)) && Number(previewPage) > 0 &&
    String(previewMaterialId) === String(previewFinding.materialId ?? previewFinding.material_id),
  )
  const actions = model?.actions || {
    canReview: false,
    canOpenSource: false,
    canRerun: false,
    canComplete: false,
  }
  // 受控筛选下与队列复用同一顺序和分母，防止选择隐藏项后详情回退、预览却不回退。
  const allReviewItems = groups
    .flatMap((group) => group.items || [])
    .filter((item) => classifyReviewItem(item))
  const reviewItems = prototypeMode && reviewFilter != null
    ? buildReviewQueueSections(groups, reviewFilter).flatMap((section) => section.items)
    : allReviewItems
  const selectFindingSource = (item) => {
    if (!item) return
    const material = item.materialId != null
      ? (model?.materialsById || {})[item.materialId] || (model?.materialsById || {})[String(item.materialId)]
      : null
    const location = sourceLocation({ finding: item, material, containerIds })
    if (location.canOpen && onSelectAndOpenSource) {
      onSelectAndOpenSource(item.findingId, location.materialId, location.page)
    } else if (location.canOpen && onOpenSource) {
      onOpenSource(location.materialId, location.page, item.findingId)
    } else {
      onClosePreview?.()
    }
  }
  const selectNextFinding = () => {
    if (!reviewItems.length) return
    const currentIndex = reviewItems.findIndex((item) => String(item.findingId) === String(selectedFindingId))
    const next = reviewItems[currentIndex >= 0 ? currentIndex + 1 : 0]
    if (!next) return
    onSelect?.(next?.findingId ?? null)
    selectFindingSource(next)
  }
  const selectPreviousFinding = () => {
    if (!reviewItems.length) return
    const currentIndex = reviewItems.findIndex((item) => String(item.findingId) === String(selectedFindingId))
    const previous = reviewItems[currentIndex - 1]
    if (!previous) return
    onSelect?.(previous?.findingId ?? null)
    selectFindingSource(previous)
  }

  // 设计 100 §6：<1280px 右栏进抽屉；≥1280px 保持两栏 grid。
  // v3 原型在 1100px 以上使用三列；1099px 以下按原型纵向堆叠三列内容。
  const prototypeWide = useMediaQuery('(min-width: 1100px)')
  const legacyWide = useMediaQuery('(min-width: 1280px)')
  const wide = prototypeMode ? prototypeWide : legacyWide
  const readingRows = Array.isArray(readingCoverage?.materials)
    ? readingCoverage.materials
    : (Array.isArray(readingCoverage?.rows) ? readingCoverage.rows : [])
  const incompleteReadingRows = readingRows.filter((row) => row && row.reading_complete !== true)
  const readingIncomplete = readingCoverage?.complete === false || incompleteReadingRows.length > 0
  const publicationMessage = publicationPolicyMessage(publication)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const triggerRef = useRef(null)
  const drawerRef = useRef(null)
  const wasOpenRef = useRef(false)

  // 视口跨过断点时收起抽屉，避免宽屏残留遮罩。
  useEffect(() => { if (wide) setDrawerOpen(false) }, [wide])

  // 抽屉打开：焦点移入抽屉、Esc 关闭、卸载时移除监听；关闭后焦点回到触发按钮。
  useEffect(() => {
    if (!drawerOpen) return undefined
    const node = drawerRef.current
    const focusables = node?.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    focusables?.[0]?.focus?.()
    const onKeyDown = (event) => {
      trapTabFocus(event, node)
      if (event.key !== 'Escape') return
      event.stopPropagation()
      setDrawerOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])
  useEffect(() => {
    if (!drawerOpen && wasOpenRef.current) triggerRef.current?.focus?.()
    wasOpenRef.current = drawerOpen
  }, [drawerOpen])

  // 刀D（设计 104 §4.3）：全屏对照入口与状态。
  // 左侧问题列复用同一个 ReviewQueue 元素；右侧原文列复用 MaterialPreviewPane（fit-width）。
  const [compareOpen, setCompareOpen] = useState(false)
  const compareTriggerRef = useRef(null)
  const compareCloseRef = useRef(null)
  const wasCompareOpenRef = useRef(false)
  const compareOriginDrawerRef = useRef(false)

  const openCompare = () => {
    compareOriginDrawerRef.current = !wide && drawerOpen
    if (selectedFinding) {
      const material = selectedFinding.material_id != null
        ? (model?.materialsById || {})[selectedFinding.material_id]
        : null
      const location = sourceLocation({ finding: selectedFinding, material, containerIds })
      if (location.canOpen && onOpenSource) {
        onOpenSource(location.materialId, location.page, selectedFinding.id ?? selectedFinding.findingId)
      }
    }
    setCompareOpen(true)
  }

  const closeCompare = useCallback(() => {
    setCompareOpen(false)
    // 窄屏入口位于右侧抽屉内；对照层关闭后保持抽屉打开，焦点才有可回退的目标。
    if (compareOriginDrawerRef.current && !wide) setDrawerOpen(true)
  }, [wide])

  const advanceAfterReview = () => {
    const pending = buildReviewQueueSections(groups, 'pending').flatMap((section) => section.items)
    const remaining = pending.filter((item) => String(item.findingId) !== String(selectedFindingId))
    if (!remaining.length) {
      closeCompare()
      onClosePreview?.()
      onReturnOverview?.()
      return
    }
    const position = allReviewItems.findIndex((item) => String(item.findingId) === String(selectedFindingId))
    const next = remaining.find((item) => allReviewItems.findIndex((candidate) => String(candidate.findingId) === String(item.findingId)) > position) || remaining[0]
    onSelect?.(next.findingId)
    selectFindingSource(next)
  }

  // 人工回执刷新可能先清空待处理队列，再完成异步跳转；同步关闭失去队列的对照层。
  useEffect(() => {
    if (!prototypeMode || !compareOpen || reviewFilter !== 'pending' || loading || submitting || reviewItems.length) return
    closeCompare()
    onClosePreview?.()
    onReturnOverview?.()
  }, [prototypeMode, compareOpen, reviewFilter, loading, submitting, reviewItems.length,
    closeCompare, onClosePreview, onReturnOverview])

  useEffect(() => {
    if (!compareOpen) return undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    compareCloseRef.current?.focus?.()
    const onKeyDown = (event) => {
      trapTabFocus(event, compareCloseRef.current?.closest?.('[role="dialog"]'))
      if (event.key !== 'Escape') return
      event.stopPropagation()
      closeCompare()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [compareOpen, closeCompare])

  // 对照层卸载后再回焦入口。卸载前的 cleanup 阶段入口按钮尚未重新挂回 DOM，
  // 直接 focus ref 会落空，浏览器会把焦点退回页面根部。
  useEffect(() => {
    if (!compareOpen && wasCompareOpenRef.current) {
      compareTriggerRef.current?.focus?.()
    }
    wasCompareOpenRef.current = compareOpen
  }, [compareOpen])

  // 对照模式下切换问题：选中即同步把该问题的 precise 原文送进右侧预览，
  // 否则“左列换一条问题、右列还停在上一条材料”会误导读者。
  const handleCompareSelect = (findingId) => {
    onSelect?.(findingId)
    const item = groups.flatMap((group) => group.items || [])
      .find((candidate) => String(candidate.findingId) === String(findingId))
    if (!item) return
    const material = item.materialId != null ? (model?.materialsById || {})[item.materialId] : null
    const location = sourceLocation({ finding: item, material, containerIds })
    if (location.canOpen && onOpenSource) {
      onOpenSource(location.materialId, location.page, findingId)
    } else {
      // 切换到不可定位的问题时清掉上一条问题的原文，避免材料和页码错配。
      onClosePreview?.()
    }
    if (prototypeMode) setCompareOpen(true)
  }

  const queueElement = (
    <ReviewQueue
      groups={groups}
      selectedFindingId={selectedFindingId}
      missCollapsed={missCollapsed}
      onToggleMiss={onToggleMiss}
      onSelect={compareOpen ? handleCompareSelect : onSelect}
      onItemSelect={prototypeMode && !compareOpen ? handleCompareSelect : undefined}
      loading={loading}
      error={error}
      filter={reviewFilter}
      onFilterChange={onFilterChange}
      onOpenSource={onOpenSource}
      onSupplement={onSupplement}
      actions={actions}
      submitting={submitting}
      onReview={onReview}
      onComplete={onComplete}
      completing={completing}
      completeDisabled={completeDisabled}
      completeLabel={completeLabel}
      materialsById={model?.materialsById || {}}
      previewContainerIds={previewContainerIds}
      emptyLabel={emptyLabel}
      hideItemActions={prototypeMode}
      prototypeMode={prototypeMode}
    />
  )

  const detailPaneElement = selectedFinding ? (
    <FindingDetailPane
      finding={selectedFinding}
      materialsById={model?.materialsById || {}}
      containerIds={containerIds}
      onOpenSource={onOpenSource}
      compact={Boolean(previewMaterial)}
      prototypeMode={prototypeMode}
      actions={actions}
      submitting={submitting}
      onReview={onReview}
      onNext={selectNextFinding}
      onCompare={openCompare}
      compareTriggerRef={compareTriggerRef}
    />
  ) : null
  const previewPaneElement = (
    <MaterialPreviewPane
      material={previewMaterial}
      page={sameSourceMaterial || previewFindingId == null ? previewPage : null}
      finding={sameSourceMaterial ? previewFinding : null}
      materials={previewMaterials}
      onMaterialChange={onPreviewMaterialChange}
      onClose={onClosePreview}
      onError={onError}
      fitWidth
    />
  )
  const emptyPreviewElement = (
    <MaterialPreviewPane
      material={null}
      materials={previewMaterials}
      onMaterialChange={onPreviewMaterialChange}
      onClose={onClosePreview}
      onError={onError}
      emptyMessage={compareOpen ? '当前问题没有可打开的原文定位，请根据规则依据进行人工核实。' : undefined}
    />
  )

  const rightColumn = (
    <>
      {selectedFinding && !prototypeMode && (
        <div data-testid="detail-toolbar" className="mb-2 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-[6px] border border-[#EFE6D8] bg-white px-3 py-2">
          <span className="min-w-0 truncate text-[12px] text-[#6B7280]">先看规则依据与匹配原文，再进入全屏对照</span>
          <button
            type="button"
            ref={compareTriggerRef}
            data-testid="open-compare-mode"
            onClick={openCompare}
            title="问题列与原文列全屏并排，可拖动分隔条"
            className="min-h-8 shrink-0 rounded-[4px] border border-[#8B7355] bg-white px-3 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9]"
          >
            全屏对照
          </button>
        </div>
      )}
      {!prototypeMode && todoPanel ? (
        <div className="mb-3 max-h-[45vh] shrink-0 overflow-y-auto xl:max-h-[260px]">{todoPanel}</div>
      ) : null}
      {previewMaterial && selectedFinding ? (
        // 88 §E 回退：打开原文时不再整块替换问题详情，而是详情（上）+ 原文（下）分区。
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div data-testid="layout-detail-stack" className="flex min-h-0 flex-[2] flex-col overflow-hidden">{detailPaneElement}</div>
          <div data-testid="layout-preview-stack" className="flex min-h-0 flex-[3] flex-col overflow-hidden">{previewPaneElement}</div>
        </div>
      ) : previewMaterial ? previewPaneElement : selectedFinding ? detailPaneElement : emptyPreviewElement}
    </>
  )

  const centerColumn = (
    <>
      {selectedFinding && !prototypeMode && (
        <div data-testid="detail-toolbar" className="mb-2 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-[6px] border border-[#EFE6D8] bg-white px-3 py-2">
          <span className="min-w-0 truncate text-[12px] text-[#6B7280]">先看规则依据与匹配原文，再进入全屏对照</span>
          <button
            type="button"
            ref={compareTriggerRef}
            data-testid="open-compare-mode"
            onClick={openCompare}
            title="问题列与原文列全屏并排，可拖动分隔条"
            className="min-h-8 shrink-0 rounded-[4px] border border-[#8B7355] bg-white px-3 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9]"
          >
            全屏对照
          </button>
        </div>
      )}
      {previewMaterial && previewFindingId == null ? previewPaneElement : overviewMode && overviewPanel ? overviewPanel : stepPanel ? stepPanel : previewMaterial && selectedFinding ? (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div data-testid="layout-detail-stack" className="flex min-h-0 flex-[2] flex-col overflow-hidden">{detailPaneElement}</div>
          <div data-testid="layout-preview-stack" className="flex min-h-0 flex-[3] flex-col overflow-hidden">{previewPaneElement}</div>
        </div>
      ) : previewMaterial ? previewPaneElement : selectedFinding ? detailPaneElement : (
        <div data-testid="layout-guide" className="flex min-h-[260px] flex-1 items-center justify-center rounded-[6px] border border-dashed border-[#DED2C0] bg-white p-6 text-center text-sm text-[#6B7280]">
          从左侧问题队列选择一项开始
        </div>
      )}
    </>
  )

  // 全屏对照时只渲染对照层：避免底层两栏与对照层重复出现同一批 testid/焦点目标。
  if (compareOpen) {
    const compareIndex = Math.max(0, reviewItems.findIndex((item) => String(item.findingId) === String(selectedFindingId)))
    const compareSubtitle = selectedFinding
      ? [selectedFinding.item_id, selectedFinding.status_label, selectedFinding.severity_label ? `风险：${selectedFinding.severity_label}` : null]
        .filter(Boolean).join(' · ')
      : null
    return (
      <ReviewCompareMode
        prototypeMode={prototypeMode}
        title={prototypeMode ? `问题 ${compareIndex + 1} / ${reviewItems.length}` : '证据对照'}
        subtitle={compareSubtitle}
        onClose={closeCompare}
        closeButtonRef={compareCloseRef}
        left={prototypeMode ? (
          <div data-testid="compare-source-pane" className="flex min-h-0 min-w-0 flex-1 flex-col">
            {previewMaterial ? previewPaneElement : emptyPreviewElement}
          </div>
        ) : queueElement}
        right={
          <div data-testid="compare-right-stack" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            {prototypeMode ? (
              <>
                <header data-testid="compare-detail-header" className="flex shrink-0 items-center gap-3 border-b border-[#E5DED3] bg-white px-4 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-semibold text-[#3B332B]">问题 {compareIndex + 1} / {reviewItems.length}</p>
                    <p className="truncate text-[11px] text-[#7A6C5A]">{compareSubtitle || '当前问题详情'}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button type="button" onClick={selectPreviousFinding} className="min-h-8 rounded-[4px] border border-[#D8CBB8] bg-white px-2.5 text-[11px] text-[#705C45] hover:bg-[#FBF5EC]">上一条</button>
                    <button type="button" onClick={selectNextFinding} className="min-h-8 rounded-[4px] border border-[#D8CBB8] bg-white px-2.5 text-[11px] text-[#705C45] hover:bg-[#FBF5EC]">下一条</button>
                    <button type="button" ref={compareCloseRef} data-testid="compare-close" onClick={closeCompare} className="min-h-8 rounded-[4px] border border-[#8B7355] bg-white px-2.5 text-[11px] text-[#8B7355] hover:bg-[#FBF5EC]">返回</button>
                  </div>
                </header>
                <CompareQuickActions
                  finding={selectedFinding}
                  actions={actions}
                  submitting={submitting}
                  onReview={onReview}
                  onNext={advanceAfterReview}
                  materialsById={model?.materialsById || {}}
                  containerIds={containerIds}
                  onOpenSource={onOpenSource}
                />
              </>
            ) : (previewMaterial ? previewPaneElement : emptyPreviewElement)}
          </div>
        }
      />
    )
  }

  return (
    <div
      data-testid="review-workbench-layout"
      data-layout={prototypeMode ? 'prototype-review-three-column' : undefined}
      data-reading-complete={readingCoverage?.complete === true ? 'true' : undefined}
      className={`relative flex w-full min-h-0 min-w-0 flex-col gap-3 overflow-x-hidden ${prototypeMode ? 'max-[1099px]:overflow-y-auto min-[1100px]:h-full min-[1100px]:overflow-hidden min-[1100px]:grid min-[1100px]:grid-cols-[288px_minmax(0,1fr)_360px] min-[1100px]:gap-[14px] min-[1280px]:grid-cols-[300px_minmax(0,1fr)_380px]' : 'xl:h-full xl:overflow-hidden xl:grid xl:grid-cols-[minmax(0,380px)_minmax(0,1fr)] xl:gap-4'} ${className}`}
    >
      {publicationMessage && (
        <section
          data-testid="publication-policy-banner"
          role="status"
          className={`col-span-full shrink-0 rounded-[6px] border px-3 py-2 text-[12px] ${publicationMessage.tone === 'failed'
            ? 'border-[#E9CACA] bg-[#FFF5F5] text-[#8A3B3B]'
            : 'border-[#E5C79C] bg-[#FFF8EC] text-[#765B36]'}`}
        >
          <div className="font-medium">{publicationMessage.title}</div>
          <div className="mt-1 text-[11px] opacity-90">{publicationMessage.detail}</div>
        </section>
      )}
      {/* <1280px：右栏收进抽屉，这里只留入口；中间（左栏）占满宽度不压缩。 */}
      {readingIncomplete && (
        <section
          data-testid="review-reading-coverage-warning"
          className="col-span-full shrink-0 rounded-[6px] border border-[#E5C79C] bg-[#FFF8EC] px-3 py-2 text-[12px] text-[#765B36]"
          role="status"
        >
          <div className="font-medium">部分页面识别不完整，可能存在模糊或遮挡，请结合原件核对。</div>
          <div className="mt-1 space-y-0.5 text-[11px] text-[#8A6B42]">
            {incompleteReadingRows.length > 0 ? incompleteReadingRows.map((row) => {
              const unread = Array.isArray(row.unread_pages) ? row.unread_pages : []
              const suspect = Array.isArray(row.suspect_pages) ? row.suspect_pages : []
              const label = row.file_name || `材料 ${row.material_id ?? ''}`
              const pages = [...new Set([...unread, ...suspect])].sort((a, b) => a - b)
              return <div key={String(row.material_id ?? label)}>{label}：{pages.length ? `第 ${pages.join('、')} 页` : '读取状态待确认'}</div>
            }) : <div>存在材料读取覆盖缺口，请先查看材料状态或补充清晰材料。</div>}
          </div>
        </section>
      )}
      {!wide && !prototypeMode && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-[#EFE6D8] bg-white px-3 py-2">
          <span className="text-[12px] text-[#6B7280]">窄屏：待办、问题详情与原文预览在抽屉中查看</span>
          <button
            type="button"
            ref={triggerRef}
            data-testid="layout-right-drawer-trigger"
            aria-expanded={drawerOpen}
            aria-controls="layout-right-drawer"
            onClick={() => setDrawerOpen(true)}
            className="min-h-9 rounded-[4px] border border-[#8B7355] bg-white px-3 text-xs text-[#8B7355] hover:bg-[#F7F1E9]"
          >
            待办与详情
          </button>
        </div>
      )}

      {prototypeMode ? (
        <>
          <aside data-testid="layout-prototype-left-column" className="flex min-h-0 w-full min-w-0 flex-col gap-3 overflow-y-auto max-[1099px]:min-h-[360px] min-[1100px]:h-full">
            {todoPanel || <div data-testid="layout-prototype-left-empty" className="rounded-[6px] border border-dashed border-[#D8E1EB] bg-white p-4 text-xs text-[#64748B]">暂无待办</div>}
          </aside>
          <main data-testid="layout-prototype-center-column" className="flex min-h-0 min-w-0 flex-col overflow-x-hidden overflow-y-auto max-[1099px]:min-h-[360px]">
            {centerColumn}
          </main>
          <aside data-testid="layout-prototype-right-column" className="flex min-h-0 min-w-0 flex-col overflow-y-auto max-[1099px]:min-h-[360px] min-[1100px]:h-full">
            {queueElement}
          </aside>
        </>
      ) : (
        <div data-testid="layout-left-column" className="flex h-full min-h-0 w-full min-w-0 flex-col gap-3 overflow-hidden xl:h-full">
          {queueElement}
        </div>
      )}

      {wide && !prototypeMode && (
        <div data-testid="layout-right-column" className="flex min-h-0 min-w-0 flex-col">
          {rightColumn}
        </div>
      )}

      {!wide && drawerOpen && (
        <div
          data-testid="layout-right-drawer-overlay"
          className="fixed inset-0 z-40 bg-black/30"
          onClick={() => setDrawerOpen(false)}
        >
          <aside
            id="layout-right-drawer"
            data-testid="layout-right-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="待办与详情"
            ref={drawerRef}
            className="absolute right-0 top-0 flex h-full w-[min(92vw,400px)] max-w-full flex-col bg-white shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex shrink-0 items-center gap-2 border-b border-[#EFE6D8] px-3 py-2">
              <h2 className="text-[13px] font-semibold text-[#8B7355]">待办与详情</h2>
              <button
                type="button"
                data-testid="layout-right-drawer-close"
                aria-label="关闭抽屉"
                onClick={() => setDrawerOpen(false)}
                className="ml-auto min-h-8 rounded px-2 text-lg leading-none text-[#9CA3AF] hover:text-[#1A1A1A]"
              >
                ×
              </button>
            </header>
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
              {prototypeMode && todoPanel ? <div className="mb-3">{todoPanel}</div> : null}
              {rightColumn}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
