import { useEffect, useState } from 'react'
import { isMaterialOnly, reviewBucket } from '../lib/reviewDecision'
import { collectContainerIds } from '../lib/materialState'
import { buildReviewQueueSections, reviewQueueCounts } from '../lib/reviewQueueModel'
import ReviewActions from './ReviewActions'

// 左栏：主体/待办分组树。
// - 组来源：U0 view-model 的 groups（kind='section' | 'subject'）。
// - 组内 item 由适配层排好序（不符合/无法核实优先 → 严重度 → 后端原序）。
// - 符合（miss）默认折叠（D6），只折叠不 filter —— 绝不丢失任何结论。
// - T9-B-R1：`queue-error` 与旧结论列表**并存** —— 失败提示只表示「最新一次读取失败」，
//   不代表已有结论失效，故不得替换/清空/遮挡列表。只有「无任何结论且读取失败」时才
//   仅显示失败原因；绝不把请求故障退化成 `queue-empty`（「核查确无结论」的语义）。

// 刀D（设计 104 §4.1）：规则依据的展示文案——文号/名称 + 条号；校对状态必须说出来，
// 不能把 OCR 未校对原文冒充已核对。这些都只读自 view-model 的 basis 视图。
function basisTitle(basis) {
  if (!basis) return ''
  const doc = basis.docNo || basis.docTitle || basis.docId || '依据库'
  const marker = basis.marker ? ` ${basis.marker}` : ''
  const pages = Number.isInteger(basis.pageStart)
    ? (Number.isInteger(basis.pageEnd) && basis.pageEnd !== basis.pageStart
      ? `（第 ${basis.pageStart}–${basis.pageEnd} 页）` : `（第 ${basis.pageStart} 页）`)
    : ''
  return `${doc}${marker}${pages}`.trim()
}

function basisVerification(basis) {
  if (!basis || basis.humanVerified == null) return '依据库未记录校对状态'
  return basis.humanVerified ? '依据原文已人工校对' : '依据原文未人工校对（OCR）'
}

function noMatchText(status) {
  if (status === 'undecidable') return '无匹配原文：未取得可核实的材料证据，需补件或人工核实'
  return '无匹配原文：本条未记录引用原文，仅有规则依据与问题描述'
}

function basisStatusText(item) {
  if (item?.basis) return basisTitle(item.basis)
  if (item?.basisStatus === 'not_completed') return '规则依据检索尚未完成'
  if (item?.basisStatus === 'not_returned') return '规则依据尚未回读，当前仅作问题提示'
  if (item?.basisStatus === 'unavailable') return '暂无固定规则依据，需人工确认'
  return '规则依据未返回，需人工确认'
}

const REVIEWABLE_STATUSES = new Set(['hit', 'undecidable'])

function QueueItem({ item, selected, onSelect, actions, submitting, onReview, materialsById, containerIds, onOpenSource, hideItemActions = false, prototypeMode = false }) {
  const material = item.materialId != null ? materialsById?.[item.materialId] : null
  const sourcePage = Number(item.page)
  const hasUsablePage = Number.isInteger(sourcePage) && sourcePage > 0
  const isPdf = typeof material?.file_name === 'string' && material.file_name.trim().toLowerCase().endsWith('.pdf')
  const materialId = material?.id ?? material?.material_id ?? item.materialId
  const canOpenSource = selected && ['precise', 'page'].includes(item.locationMode) && hasUsablePage &&
    material?.parse_state === 'done' && isPdf && !containerIds.has(String(materialId)) && onOpenSource
  const source = typeof item.source === 'string' ? item.source.trim() : ''
  const quote = typeof item.quote === 'string' ? item.quote.trim() : ''
  // 只展示后端实际返回的分析字段；不使用通用模板冒充 AI 思考链。
  const analysis = typeof item.analysis === 'string' ? item.analysis.trim()
    : typeof item.rule_reason === 'string' ? item.rule_reason.trim()
      : typeof item.ruleReason === 'string' ? item.ruleReason.trim() : ''
  const hasReviewResult = item.review && ['accepted', 'ignored'].includes(reviewBucket(item))
  const showReviewResult = selected && hasReviewResult
  const canReview = selected && REVIEWABLE_STATUSES.has(item.status) && reviewBucket(item) === 'pending' && actions.canReview
  return (
    <li className={prototypeMode
      ? `border-b border-[#DCE5EE] bg-white p-3 last:border-b-0 ${selected ? 'border-l-4 border-l-[#174C7E] bg-[#EAF2FB]' : ''}`
      : `rounded-[6px] border bg-white p-2.5 ${selected ? 'border-[#8B7355] ring-1 ring-[#8B7355]/30' : 'border-[#E5E5E5]'}`}
      data-finding-card={item.findingId} data-testid={`finding-card-${item.findingId}`}>
      <button
        type="button"
        onClick={() => onSelect?.(item.findingId)}
        aria-current={selected ? 'true' : undefined}
        data-finding-id={item.findingId}
        data-selected={selected ? 'true' : 'false'}
        data-status={item.status}
        className={`flex w-full items-start gap-2 rounded-[4px] border px-2 py-1.5 text-left transition-colors ${
          selected
            ? (prototypeMode ? 'border-transparent bg-transparent' : 'border-[#8B7355] bg-[#F7F1E9]')
            : (prototypeMode ? 'border-transparent hover:bg-[#FBFDFF]' : 'border-transparent hover:bg-[#FDFAF4]')
        }`}
      >
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            {!prototypeMode && item.itemId && (
              <span className="rounded border border-[#EFE6D8] bg-white px-1 py-0.5 text-[10px] leading-none text-[#8B7355]">
                {item.itemId}
              </span>
            )}
            {prototypeMode && hasReviewResult && (
              <span className={`rounded-full border px-2 py-0.5 text-[10px] leading-none ${
                hasReviewResult ? 'border-[#C8E4CF] bg-[#EFFAF2] text-[#2F7D49]' : 'border-[#EAD5AA] bg-[#FFF8E9] text-[#96651C]'
              }`}>
                {reviewBucket(item) === 'accepted' ? '已采纳' : '已忽略'}
              </span>
            )}
            {/* 机器状态与“未复核”不在问题卡重复展示；待处置数量和采纳/忽略筛选已表达处理状态。 */}
            {item.invalidated && (
              <span data-testid={`finding-invalidated-${item.findingId}`} className="rounded border border-[#F0DCB8] bg-[#FFFDF8] px-1 py-0.5 text-[10px] leading-none text-[#92400E]">
                原复核意见失效
              </span>
            )}
          </span>
          <span className="mt-1 block break-words text-[13px] leading-snug text-[#1A1A1A] line-clamp-3">
            {item.title}
          </span>
            {prototypeMode && <span className="mt-1 block break-words text-[11px] text-[#587089]">{basisStatusText(item) || '已保留处理记录'}</span>}
        </span>
      </button>
      {!prototypeMode && selected && (item.basis || source || analysis || quote || (!quote && item.status !== 'miss')) && <div data-testid="selected-finding-detail" className="mt-2 space-y-1 rounded-[4px] bg-[#FAF8F4] px-2 py-1.5 text-[11px] leading-relaxed text-[#6B7280]">
        {/* 刀D（设计 104 §4.1）：先给“触犯哪条规则”，再给结论材料；查不到坐标时如实说明。 */}
        {item.basis ? <div data-testid="selected-finding-basis" className="break-words">
          <p><span className="font-medium text-[#4B5563]">规则依据：</span>{basisTitle(item.basis)}</p>
          {item.basis.text && <p className="mt-0.5 line-clamp-3">{item.basis.text}</p>}
          <p className="mt-0.5 text-[10px] text-[#9CA3AF]">{basisVerification(item.basis)}</p>
        </div> : <p className="break-words text-[10px] text-[#9CA3AF]">该条目未建立条款坐标（依据库未收录）</p>}
        {source && <p className="break-words"><span className="font-medium text-[#4B5563]">参考依据：</span>{source}</p>}
        {analysis && <p className="break-words"><span className="font-medium text-[#4B5563]">分析说明：</span>{analysis}</p>}
        {quote && <p className="break-words"><span className="font-medium text-[#4B5563]">材料摘录：</span>{quote}</p>}
        {!quote && item.status !== 'miss' && <p data-testid="selected-finding-no-match" className="break-words text-[10px] text-[#9CA3AF]">{noMatchText(item.status)}</p>}
      </div>}
      {prototypeMode && canOpenSource && <button type="button" data-testid="open-source" onClick={() => onOpenSource(item.materialId, sourcePage, item.findingId)} className="sr-only">
        {item.locationMode === 'page' ? `仅有页码线索：第 ${sourcePage} 页 · 定位原文` : `${material.file_name} · 第 ${sourcePage} 页 · 定位原文`}
      </button>}
      {prototypeMode && !canOpenSource && item.locationMode === 'page' && item.page && <span className="sr-only">仅有页码线索：第 {item.page} 页</span>}
      {prototypeMode && !canOpenSource && item.locationMode === 'unresolved' && <span className="sr-only">暂未定位到具体页</span>}
      {!prototypeMode && (canOpenSource ? <button type="button" data-testid="open-source" onClick={() => onOpenSource(item.materialId, sourcePage, item.findingId)} className="mt-1 min-h-8 max-w-full truncate text-left text-[11px] text-[#8B7355] hover:underline">
        {item.locationMode === 'page' ? `仅有页码线索：第 ${sourcePage} 页 · 定位原文` : `${material.file_name} · 第 ${sourcePage} 页 · 定位原文`}
      </button> : item.locationMode === 'page' && item.page ? <p className="mt-1 text-[11px] text-[#9CA3AF]">仅有页码线索：第 {item.page} 页</p> : item.locationMode === 'unresolved' ? <p className="mt-1 text-[11px] text-[#9CA3AF]">暂未定位到具体页</p> : item.locationMode === 'precise' && material == null ? <p className="mt-1 text-[11px] text-[#9CA3AF]">材料不可用，暂无法定位原文</p> : item.locationMode === 'precise' && material?.parse_state !== 'done' ? <p className="mt-1 text-[11px] text-[#9CA3AF]">材料尚未解析完成，暂无法定位原文</p> : item.locationMode === 'precise' && (!isPdf || containerIds.has(String(materialId))) ? <p className="mt-1 text-[11px] text-[#9CA3AF]">该材料暂不支持原文预览</p> : item.locationMode === 'precise' && !hasUsablePage ? <p className="mt-1 text-[11px] text-[#9CA3AF]">暂未定位到具体页</p> : null)}
      {!hideItemActions && (showReviewResult || canReview) && <ReviewActions
        compact
        finding={{ ...item, id: item.findingId, message: item.message || item.title, status_label: item.statusLabel }}
        item={item}
        evidence={item.quote ? [{ quote: item.quote }] : []}
        actions={actions}
        submitting={submitting}
        onReview={onReview}
      />}
    </li>
  )
}

function GroupBlock({ group, selectedFindingId, onSelect, actions, submitting, onReview, materialsById, containerIds, onOpenSource, hideItemActions = false, prototypeMode = false }) {
  const items = group.items || []
  return (
    <div className="mb-3 last:mb-0" data-group-key={group.key} data-group-kind={group.kind}>
      <div className="flex items-baseline gap-2 px-1.5 py-1">
        <h3 className="min-w-0 truncate text-xs font-semibold text-[#6B7280]">{group.label}</h3>
        <span className="ml-auto shrink-0 text-[10px] tabular-nums text-[#9CA3AF]">
          <b className="text-emerald-700">通过 {group.missCount || 0}</b>
          {' · '}
          <b className="text-amber-600">待处理 {(group.hitCount || 0) + (group.undecidableCount || 0)}</b>
        </span>
      </div>
      <ul className="space-y-0.5">
        {items.map((item) => (
          <QueueItem
            key={item.findingId}
            item={item}
            selected={item.findingId === selectedFindingId}
            onSelect={onSelect}
            actions={actions}
            submitting={submitting}
            onReview={onReview}
            materialsById={materialsById}
            containerIds={containerIds}
            onOpenSource={onOpenSource}
            hideItemActions={hideItemActions}
            prototypeMode={prototypeMode}
          />
        ))}
      </ul>
    </div>
  )
}

/**
 * @param {object}   props
 * @param {object[]} [props.groups]             U0 view-model 的 groups
 * @param {number}   [props.selectedFindingId]
 * @param {boolean}  [props.missCollapsed]      符合项是否折叠（默认 true）
 * @param {Function} [props.onToggleMiss]
 * @param {Function} [props.onSelect]           (findingId) => void
 * @param {boolean}  [props.loading]
 * @param {string}   [props.error]              平实错误原因（拉取结论失败）
 */
export default function ReviewQueue({
  groups = [],
  selectedFindingId = null,
  onSelect,
  onItemSelect,
  loading = false,
  error = null,
  filter: controlledFilter,
  onFilterChange,
  actions = {},
  submitting = false,
  onReview,
  onComplete,
  completing = false,
  completeDisabled = false,
  completeLabel = '生成报告',
  materialsById = {},
  previewContainerIds = [],
  onOpenSource,
  hideItemActions = false,
  prototypeMode = false,
  // V2：五步卡/待办分组筛选后可能真的没有结论。此时空态不得沿用
  // 「暂无核查结论——先运行核查」（核查已跑过，只是被筛掉），由上层给出诚实文案。
  emptyLabel = null,
}) {
  const [localFilter, setLocalFilter] = useState('pending')
  const filter = controlledFilter ?? localFilter
  const list = groups || []
  const materials = Object.values(materialsById || {})
  const storedContainerIds = materials
    .filter((material) => material?.parse_state === 'stored')
    .map((material) => material.id ?? material.material_id)
  const containerIds = new Set(
    [...collectContainerIds(materials, [...storedContainerIds, ...previewContainerIds])].map((id) => String(id)),
  )
  const matchesFilter = (item) => {
    if (isMaterialOnly(item)) return false
    if (!prototypeMode) {
      return filter === 'all' || (REVIEWABLE_STATUSES.has(item.status) && reviewBucket(item) === filter)
    }
    if (filter === 'all') return REVIEWABLE_STATUSES.has(item.status)
    if (filter === 'pending') return REVIEWABLE_STATUSES.has(item.status) && reviewBucket(item) === 'pending'
    if (filter === 'processed') return REVIEWABLE_STATUSES.has(item.status) && ['accepted', 'ignored'].includes(reviewBucket(item))
    if (filter === 'undecidable') return item.status === 'undecidable'
    if (filter === 'hit') return item.status === 'hit'
    return false
  }
  const filtered = list.map((group) => ({
    ...group,
    items: (group.items || []).filter(matchesFilter),
  })).filter((group) => group.items.length)
  useEffect(() => {
    if (controlledFilter !== undefined) return
    const visible = filtered.flatMap((group) => group.items)
    if (!visible.some((item) => item.findingId === selectedFindingId)) onSelect?.(visible[0]?.findingId ?? null)
  }, [groups, filter, selectedFindingId, onSelect, controlledFilter])
  const openTotal = list.reduce((n, g) => n + (g.openCount || 0), 0)
  const allItems = list.flatMap((group) => group.items || []).filter((item) => !isMaterialOnly(item))
  const issueItems = allItems.filter((item) => REVIEWABLE_STATUSES.has(item.status))
  const bucketCounts = {
    pending: issueItems.filter((item) => reviewBucket(item) === 'pending').length,
    accepted: issueItems.filter((item) => reviewBucket(item) === 'accepted').length,
    ignored: issueItems.filter((item) => reviewBucket(item) === 'ignored').length,
    all: prototypeMode ? issueItems.length : allItems.length,
  }
  const prototypeCounts = reviewQueueCounts(list)
  const prototypeSections = buildReviewQueueSections(list, filter)

  return (
    <aside
      aria-label="待办列表"
      data-testid="review-queue"
      className="flex min-h-0 flex-1 w-full min-w-0 flex-col overflow-hidden rounded-[6px] border border-[#EFE6D8] bg-white"
    >
      {prototypeMode ? (
        <>
          <header className="flex items-center gap-2 border-b border-[#DCE5EE] px-3 py-2">
            <h2 className="text-[13px] font-semibold text-[#16283D]">人工复核</h2>
            <span className="ml-auto text-[11px] tabular-nums text-[#64748B]">待复核 {prototypeCounts.undecidable + prototypeCounts.hit}</span>
          </header>
          <div className="grid grid-cols-4 gap-1 border-b border-[#DCE5EE] p-1" aria-label="问题筛选">
          {[
            ['pending', '待处理'],
            ['undecidable', '无法核实'],
            ['processed', '已处理'],
          ].map(([key, label]) => (
            <button key={key} type="button" data-testid={`queue-filter-${key}`} aria-pressed={filter === key}
              onClick={() => { setLocalFilter(key); onFilterChange?.(key) }}
              className={`min-h-10 rounded-[4px] text-[11px] ${filter === key ? 'bg-[#EAF2FB] font-semibold text-[#174C7E]' : 'text-[#587089] hover:bg-[#FBFDFF]'}`}>
              {label} <span className="tabular-nums">{key === 'pending' ? prototypeCounts.undecidable + prototypeCounts.hit : key === 'undecidable' ? prototypeCounts.undecidable : prototypeCounts.accepted + prototypeCounts.ignored}</span>
            </button>
          ))}
          <button type="button" className="sr-only" data-testid="queue-filter-all" aria-pressed={filter === 'all'} onClick={() => { setLocalFilter('all'); onFilterChange?.('all') }}>全部问题</button>
          <span className="sr-only">未人工处理</span>
          </div>
        </>
      ) : (
        <>
          <header className="flex items-center gap-2 border-b border-[#EFE6D8] px-3 py-2">
            <h2 className="text-sm font-semibold text-[#8B7355]">人工复核</h2>
            <span className="ml-auto text-xs tabular-nums text-[#9CA3AF]">待办 {openTotal}</span>
          </header>
          <p className="border-b border-[#F2ECE4] px-3 py-2 text-[11px] leading-relaxed text-[#6B7280]">系统结论须人工确认后进入意见书；未复核的问题将在意见书中标注“未经人工复核”。</p>
          <div className="grid grid-cols-4 border-b border-[#EFE6D8] p-1" aria-label="问题处理状态">
            {[
              ['pending', '待处理'],
              ['accepted', '已采纳'],
              ['ignored', '已忽略'],
              ['all', '全部'],
            ].map(([key, label]) => (
              <button key={key} type="button" data-testid={`queue-filter-${key}`} aria-pressed={filter === key}
                onClick={() => { setLocalFilter(key); onFilterChange?.(key) }}
                className={`min-h-11 rounded-[4px] text-[11px] ${filter === key ? 'bg-[#F7F1E9] font-semibold text-[#8B7355]' : 'text-[#6B7280] hover:bg-[#fafafa]'}`}>
                {label} <span className="tabular-nums">{bucketCounts[key]}</span>
              </button>
            ))}
          </div>
        </>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {/* T9-B-R1：失败提示与旧结论并存 —— `queue-error` 走普通流布局置于列表之上，
            绝不替换/清空/遮挡列表（不使用绝对定位；窄宽度下自然换行，不与队列操作重叠）。 */}
        {!loading && error && (
          <p
            data-testid="queue-error"
            className="mb-2 rounded-[6px] border border-[#F3C9C9] bg-[#FDF3F3] px-2.5 py-2 text-[12px] leading-relaxed text-[#C62828]"
          >
            结论加载失败：{error}
          </p>
        )}

        {loading ? (
          <p className="p-3 text-center text-xs text-[#9CA3AF]">加载中…</p>
        ) : list.length ? (
          filtered.length ? (prototypeMode
            ? prototypeSections.map((section) => {
              const ids = new Set(section.items.map((item) => item.findingId))
              return <section key={section.key} data-testid={`review-queue-section-${section.key}`} className="mb-3 last:mb-0">
                {list.map((group) => {
                  const items = (group.items || []).filter((item) => ids.has(item.findingId))
                  if (!items.length) return null
                  return <div key={group.key} data-group-key={group.key} data-group-kind={group.kind}>
                    <h3 className="px-3 py-2 text-xs font-semibold text-[#587089]">{group.label}</h3>
                    <ul>{items.map((item) => (
                      <QueueItem
                        key={item.findingId}
                        item={item}
                        selected={item.findingId === selectedFindingId}
                        onSelect={onItemSelect || onSelect}
                        actions={actions}
                        submitting={submitting}
                        onReview={onReview}
                        materialsById={materialsById}
                        containerIds={containerIds}
                        onOpenSource={onOpenSource}
                        hideItemActions={hideItemActions}
                        prototypeMode
                      />
                    ))}</ul>
                  </div>
                })}
              </section>
            })
            : filtered.map((group) => (
            <GroupBlock
              key={group.key}
              group={group}
              selectedFindingId={selectedFindingId}
                onSelect={onItemSelect || onSelect}
              actions={actions}
              submitting={submitting}
              onReview={onReview}
              materialsById={materialsById}
              containerIds={containerIds}
              onOpenSource={onOpenSource}
              hideItemActions={hideItemActions}
              prototypeMode={prototypeMode}
            />
          ))) : <p data-testid="queue-filter-empty" className="p-4 text-center text-[13px] text-[#6B7280]">暂无{filter === 'accepted' ? '已采纳' : filter === 'ignored' ? '已忽略' : '待处理'}项</p>
        ) : error ? null : (
          // 有失败时上方已给出原因，此处不再显示空态 —— 避免把「读取失败」伪装成
          // 「暂无核查结论」；只有「确无结论且无失败」才是诚实空态。
          <p data-testid="queue-empty" className="p-4 text-center text-[13px] text-[#9CA3AF]">
            {emptyLabel || '暂无核查结论——先运行核查'}
          </p>
        )}
      </div>
      {onComplete && <footer className="shrink-0 border-t border-[#EFE6D8] bg-white p-2">
        <p className="mb-1 text-[11px] text-[#6B7280]">已复核 {prototypeMode ? prototypeCounts.accepted + prototypeCounts.ignored : bucketCounts.accepted + bucketCounts.ignored} / {prototypeMode ? prototypeCounts.total : bucketCounts.all}</p>
        {prototypeCounts.undecidable > 0 && <p className="mb-2 text-[11px] leading-relaxed text-[#92400E]">还有 {prototypeCounts.undecidable} 项无法核实，可查看原文并处理。</p>}
        <button type="button" onClick={onComplete} disabled={completing || completeDisabled} aria-disabled={completeDisabled || undefined} className="min-h-10 w-full rounded-[4px] bg-[#8B7355] px-3 py-2 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
          {completing ? '正在生成报告…' : completeLabel}
        </button>
      </footer>}
    </aside>
  )
}
