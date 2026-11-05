import { useRef, useState } from 'react'
import { ChevronDown, FileText, Trash2 } from 'lucide-react'
import { buildMaterialLedger } from '../lib/materialLedger'

// 材料核验台账（V1）：一份材料一行，集中显示「归类 / 主体 / 关键要素 / 读取状态 / 动作」。
// - 行内状态文案与可预览判定全部来自 `lib/materialLedger`（复用 `materialState` 单一表源），
//   组件不重新判断材料类别、必交关系或解析状态。
// - `mode="setup"`：材料阶段，可改类型、给报审包及其子材料绑定主体、移除材料、**替换**（仅启动前）；
//   `mode="overview"`：核查阶段只读，可打开原件。
// - 页数/关键要素字段当前接口未完整返回，缺就如实空着，不猜、不伪造。
//
// 刀B（98 §6.2）：替代链只在台账里展示与发起，不做文件名/类别启发式匹配——
// 「替换」只把用户选中的新材料 id 与目标 id 交给父组件，上传字段
// `replaces_material_id` 由 lib/api 统一携带；旧行仍可取原文、只留痕。

const FACT_LABELS = {
  contract_section: '合同段',
  sign_date: '签订日期',
  contract_type: '合同类型',
  amount: '合同金额',
  scope: '施工范围',
  parties: '合同主体',
}

const HISTORY_WARNING_LABELS = {
  missing_target: '替代目标缺失',
  cross_task_edge: '替代链跨任务',
  multiple_predecessors: '存在多个前置版本',
  multiple_container_children: '同一 ZIP 子文件重复',
  missing_container_child: '父 ZIP 缺少对应子文件',
  cycle: '替代链存在循环',
}

function historyWarningText(warning) {
  return HISTORY_WARNING_LABELS[warning?.code] || warning?.message || '版本链存在异常'
}

function toneClass(tone) {
  if (tone === 'bad') return 'border-[#F5C6C6] bg-[#FFF7F7] text-[#C62828]'
  if (tone === 'warn') return 'border-[#F0DCB8] bg-[#FFFDF8] text-[#92400E]'
  if (tone === 'ok') return 'border-[#CFE8D4] bg-[#F6FCF7] text-[#2E7D32]'
  return 'border-[#D8E1EB] bg-[#F8FAFC] text-[#64748B]'
}

function readingSummary(row) {
  if (row?.parseKind === 'parsing' || row?.parseKind === 'uploaded') return '有读取提示，请查看详情（正在读取）'
  if (row?.parseKind === 'preparing' || row?.parseKind === 'stored') return '资料包待准备，详情展开后查看'
  if (row?.tone === 'bad') return '读取受限，可能影响核查覆盖'
  if (row?.tone === 'warn') return '有读取提示，请查看详情'
  return row?.parseLabel || '读取状态待确认'
}

// 未读页徽标的唯一数据源：报告 `reading_coverage[]`（设计 98 §8）。优先按 material_id
// 精确匹配（同名材料重传是常态，只按文件名会把徽标挂错行），老响应无 id 时回落文件名。
// 后端材料 DTO 不透出页数，前端不另算、不解析降级文案里的数字、不伪造字段。
function unreadPagesFor(row, coverageByMaterial, coverageByName) {
  const coverage = coverageByMaterial?.[row.id] ?? coverageByName?.[row.fileName]
  const pages = coverage?.unread_pages
  // 报告 reading_coverage[].unread_pages 是页码数组（后端 parsing.reading_coverage 形状）。
  if (Array.isArray(pages)) return pages.length > 0 ? pages.length : null
  const n = Number.isInteger(pages) ? pages : null
  return n != null && n > 0 ? n : null
}

function RoleControl({ row, roleOptions, disabled, lockReason, onChangeRole }) {
  const [open, setOpen] = useState(false)
  if (!onChangeRole || !roleOptions?.length) {
    return <span className="rounded-full bg-[#EAF2FB] px-2 py-0.5 text-[11px] text-[#143B63]">{row.roleLabel}</span>
  }
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        disabled={disabled}
        aria-label={`修改材料类型：${row.roleLabel}`}
        aria-expanded={open}
        aria-haspopup="menu"
        title={lockReason || undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            setOpen(false)
          }
        }}
        className="rounded-full border border-dashed border-[#8B9DB0] px-2 py-0.5 text-[11px] text-[#385A77] hover:bg-[#EAF2FB] disabled:opacity-50"
      >
        类型：{row.roleLabel} <ChevronDown className="inline h-3 w-3" />
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full z-30 mt-1 min-w-[150px] rounded-[6px] border border-[#D8E1EB] bg-white py-1 shadow-lg">
          {roleOptions.map((role) => (
            <button
              key={role.key}
              type="button"
              role="menuitem"
              onClick={() => { setOpen(false); if (role.key !== row.role) onChangeRole(row.id, role.key) }}
              className={`block w-full px-3 py-1.5 text-left text-[12px] hover:bg-[#EAF2FB] ${role.key === row.role ? 'font-semibold text-[#143B63]' : 'text-[#4B5563]'}`}
            >
              {role.label}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}

export default function MaterialLibrary({
  detail,
  containerIds = new Set(),
  onOpen,
  mode = 'overview',
  roleOptions = [],
  onChangeRole,
  onChangeSubject,
  subjectOptions = [],
  onRemove,
  onReplace,
  accept = '.pdf,application/pdf,.zip,application/zip',
  readingCoverage = [],
  busy = false,
  lockReason = null,
  onLoadHistory,
  prototypeUpload = false,
}) {
  const ledger = buildMaterialLedger(detail, containerIds)
  const setupMode = mode === 'setup'
  // `mode` 只决定首次挂载状态：概览收起、材料配置展开。后续详情刷新不得覆盖用户操作。
  const [expanded, setExpanded] = useState(setupMode)
  const replaceInputRef = useRef(null)
  const replaceTargetRef = useRef(null)
  const [historyById, setHistoryById] = useState({})
  const [historyLoadingId, setHistoryLoadingId] = useState(null)
  const [historyErrorById, setHistoryErrorById] = useState({})
  const [expandedMessages, setExpandedMessages] = useState({})
  if (!ledger.rows.length) return null
  const hasFacts = ledger.rows.some((row) => row.keyFacts.length > 0)
  const coverageByName = Object.fromEntries(
    // 只把「没有 material_id」的旧响应行按文件名兜底：带 id 的行必须精确匹配，
    // 否则一件新材料的未读页会被同名旧材料行冒领（同 id 重传同名文件很常见）。
    (Array.isArray(readingCoverage) ? readingCoverage : [])
      .filter((item) => item && item.file_name && item.material_id == null)
      .map((item) => [item.file_name, item]),
  )
  const coverageByMaterial = Object.fromEntries(
    (Array.isArray(readingCoverage) ? readingCoverage : [])
      .filter((item) => item && item.material_id != null)
      .map((item) => [item.material_id, item]),
  )
  const requestReplace = (materialId) => {
    replaceTargetRef.current = materialId
    replaceInputRef.current?.click()
  }
  const loadHistory = async (materialId) => {
    if (!onLoadHistory) return
    setHistoryErrorById((current) => ({ ...current, [materialId]: null }))
    setHistoryLoadingId(materialId)
    try {
      const history = await onLoadHistory(materialId)
      setHistoryById((current) => ({ ...current, [materialId]: history }))
    } catch (error) {
      setHistoryErrorById((current) => ({
        ...current,
        [materialId]: error?.message || '历史版本读取失败，请稍后重试',
      }))
    } finally {
      setHistoryLoadingId(null)
    }
  }

  return (
    <details
      data-testid="material-ledger"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
      className="rounded-[8px] border border-[#D8E1EB] bg-white shadow-sm"
    >
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 border-b border-[#D8E1EB] px-4 py-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] [&::-webkit-details-marker]:hidden">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#EAF2FB] text-[#143B63]"><FileText className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold text-[#16283D]">材料核验台账</h2>
          <p data-testid="material-summary" className="mt-0.5 text-[11px] text-[#64748B]">
            {prototypeUpload
              ? `共 ${ledger.summary.total} 份 · 归类可逐份更正`
              : `共 ${ledger.summary.total} 份 · 已读取 ${ledger.summary.ready} · 处理中 ${ledger.summary.processing} · 需关注 ${ledger.summary.attention}`}
          </p>
        </div>
        <ChevronDown aria-hidden="true" className={`h-4 w-4 text-[#64748B] transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </summary>

      <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(160px,1fr)_auto] gap-3 border-b border-[#E8EEF4] bg-[#F8FAFC] px-4 py-2 text-[11px] font-medium text-[#64748B] lg:grid">
        <span>{prototypeUpload ? '材料文件' : '材料与归属'}</span>{hasFacts && !prototypeUpload ? <span>关键要素</span> : <span>{prototypeUpload ? '归类与主体' : '读取提示'}</span>}<span>操作</span>
      </div>
      <ul className="divide-y divide-[#E8EEF4]">
        {ledger.rows.map((row) => {
          const unread = unreadPagesFor(row, coverageByMaterial, coverageByName)
          return (
          <li key={row.id} data-testid={`material-row-${row.id}`} className="grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1.5fr)_minmax(160px,1fr)_auto]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="min-w-0 truncate text-[13px] font-semibold text-[#16283D]" title={row.fileName}>{row.fileName}</h3>
                <RoleControl row={row} roleOptions={roleOptions} disabled={busy || Boolean(lockReason)}
                  lockReason={lockReason} onChangeRole={onChangeRole} />
                {row.isContainer && <span data-testid={`material-container-${row.id}`} className="rounded-full border border-[#D8E1EB] px-2 py-0.5 text-[11px] text-[#64748B]">报审包容器</span>}
                {setupMode && row.containerMaterialId != null && row.subjectId == null && (
                  <span data-testid={`material-unassigned-${row.id}`} className="rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-0.5 text-[11px] text-[#92400E]">
                    子材料待归类 · 启动前必填
                  </span>
                )}
                {row.supersededById != null && (
                  <span data-testid={`material-superseded-${row.id}`} className="rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-0.5 text-[11px] text-[#92400E]">已被替换</span>
                )}
                {row.isCurrent && (
                  <span data-testid={`material-current-${row.id}`} className="rounded-full border border-[#CFE8D4] bg-[#F6FCF7] px-2 py-0.5 text-[11px] text-[#2E7D32]">当前版本</span>
                )}
                {onLoadHistory && (row.isCurrent || row.supersededById != null || row.revisionNo != null) && (
                  <button
                    type="button"
                    data-testid={`material-history-toggle-${row.id}`}
                    aria-expanded={Boolean(historyById[row.id])}
                    aria-controls={`material-history-${row.id}`}
                    onClick={() => historyById[row.id]
                      ? setHistoryById((current) => { const next = { ...current }; delete next[row.id]; return next })
                      : loadHistory(row.id)}
                    className="rounded-full border border-[#B8C9D9] px-2 py-0.5 text-[11px] text-[#385A77] hover:bg-[#EAF2FB]"
                  >
                    {historyById[row.id] ? '收起版本链' : '查看版本链'}
                  </button>
                )}
              </div>
              {!prototypeUpload && (
                <p className="mt-1 text-[11px] text-[#64748B]">
                  {row.subjectName || '主体待确认'}{row.pages ? ` · ${row.pages} 页` : ''}
                </p>
              )}
              {row.supersededById != null && (
                <p data-testid={`material-superseded-target-${row.id}`} className="mt-1 text-[11px] leading-relaxed text-[#92400E]">
                  替代为：
                  {!setupMode && onOpen && row.supersededByCanPreview ? (
                    <button type="button" onClick={() => onOpen(row.supersededById)}
                      className="underline decoration-dotted underline-offset-2 hover:text-[#5B4A2F]">
                      {row.supersededByName || `材料 ${row.supersededById}`}
                    </button>
                  ) : (row.supersededByName || `材料 ${row.supersededById}`)}
                </p>
              )}
              {row.source && <p data-testid={`material-source-${row.id}`} className={prototypeUpload ? 'sr-only' : 'mt-1 truncate text-[11px] text-[#94A3B8]'} title={row.source}>来源：{row.source}</p>}

              {setupMode && row.role === 'packet' && (
                <div className="mt-2 flex items-center gap-2 text-[11px] text-[#64748B]">
                  <span className="shrink-0">关联主体</span>
                  {subjectOptions.length > 0 ? (
                    <select
                      data-testid={`material-subject-${row.id}`}
                      value={row.subjectId == null ? '' : String(row.subjectId)}
                      disabled={busy || Boolean(lockReason)}
                      title={lockReason || undefined}
                      onChange={(e) => onChangeSubject?.(row.id, e.target.value)}
                      className="min-w-0 rounded-[4px] border border-[#D8E1EB] bg-white px-2 py-1 text-[12px] text-[#4B5563] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:opacity-60"
                    >
                      <option value="">请选择分包主体</option>
                      {subjectOptions.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                    </select>
                  ) : (
                    <span className="text-[#94A3B8]">请先上传专业分包或劳务合作合同</span>
                  )}
                </div>
              )}

              {row.messages.length > 0 && (
                <div className={prototypeUpload ? 'sr-only' : `mt-2 space-y-0.5 text-[11px] leading-relaxed ${row.tone === 'bad' ? 'text-[#C62828]' : 'text-[#92400E]'}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span data-testid={`material-reading-summary-${row.id}`}>{readingSummary(row)}</span>
                    <button
                      type="button"
                      data-testid={`material-reading-toggle-${row.id}`}
                      aria-expanded={Boolean(expandedMessages[row.id])}
                      aria-controls={`material-reading-details-${row.id}`}
                      onClick={() => setExpandedMessages((current) => ({
                        ...current,
                        [row.id]: !current[row.id],
                      }))}
                      className="rounded border border-current/30 px-1.5 py-0.5 text-[10px] hover:bg-white"
                    >
                      {expandedMessages[row.id] ? '收起读取详情' : '查看读取详情'}
                    </button>
                  </div>
                  {expandedMessages[row.id] && (
                    <div id={`material-reading-details-${row.id}`} data-testid={`material-reading-details-${row.id}`} className="mt-1 rounded border border-current/20 bg-white/70 px-2 py-1.5">
                      {row.messages.map((message, index) => <p key={index}>· {message}</p>)}
                    </div>
                  )}
                </div>
              )}
              {row.subjectConflict && (
                <div data-testid={`material-subject-conflict-${row.id}`} className="mt-2 rounded border border-[#F0C7C7] bg-[#FFF7F7] px-2 py-1.5 text-[11px] leading-relaxed text-[#9B2C2C]">
                  <p className="font-medium">主体校验未通过</p>
                  {row.subjectConflict.expectedSubjectName && <p>当前主体：{row.subjectConflict.expectedSubjectName}</p>}
                  {row.subjectConflict.detectedSubjectName && <p>识别主体：{row.subjectConflict.detectedSubjectName}</p>}
                  {row.subjectConflict.confidence && <p>匹配置信度：{row.subjectConflict.confidence === 'high' ? '高' : row.subjectConflict.confidence}</p>}
                </div>
              )}
            </div>

            <div className={prototypeUpload ? 'sr-only' : 'min-w-0 rounded-[6px] bg-[#F8FAFC] px-3 py-2 text-[11px] text-[#385A77]'}>
              {row.keyFacts.length > 0
                ? <div className="space-y-1">{row.keyFacts.map(([key, value]) => (
                  <p key={key} className="truncate" title={String(value)}><b className="font-medium text-[#64748B]">{FACT_LABELS[key] || key}</b>：{String(value)}</p>
                ))}</div>
                : <p className="text-[#94A3B8]">{setupMode ? '读取完成后在此显示关键要素' : '关键要素待核对'}</p>}
            </div>

            <div className="flex min-w-[118px] flex-wrap items-start justify-end gap-2 lg:flex-col lg:items-end">
              <span data-testid={`material-state-${row.id}`} className={prototypeUpload ? 'sr-only' : `rounded-full border px-2 py-0.5 text-[11px] ${toneClass(row.tone)}`}>{row.parseLabel}</span>
              {unread != null && (
                <span data-testid={`material-unread-${row.id}`} className={prototypeUpload ? 'sr-only' : 'rounded-full border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-0.5 text-[11px] text-[#92400E]'}>未读 {unread} 页</span>
              )}
              {row.canPreview && onOpen && (
                <button type="button" aria-label={`预览 ${row.fileName}`} onClick={() => onOpen(row.id)}
                  className="min-h-11 rounded-[5px] border border-[#B8C9D9] px-2.5 text-[11px] text-[#143B63] hover:bg-[#EAF2FB] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63]">
                  查看原文
                </button>
              )}
              {setupMode && onReplace && row.supersededById == null && (
                <button type="button" disabled={busy || Boolean(lockReason)}
                  data-testid={`material-replace-${row.id}`}
                  aria-label={`替换 ${row.fileName}`}
                  title={lockReason || '上传一份新材料替换本材料；旧材料保留留痕、仍可查看原文'}
                  onClick={() => requestReplace(row.id)}
                  className="min-h-11 rounded-[5px] border border-[#DED2C0] px-2.5 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] disabled:opacity-40">
                  替换
                </button>
              )}
              {setupMode && onRemove && (
                <button type="button" disabled={busy || Boolean(lockReason)}
                  aria-label={`移除 ${row.fileName}`} title={lockReason || '移除'}
                  onClick={() => onRemove(row.id)}
                  className="min-h-11 min-w-11 rounded-[5px] border border-[#E5E7EB] px-2 text-[#9CA3AF] hover:border-[#F5C6C6] hover:text-[#C62828] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#143B63] disabled:opacity-40">
                  <Trash2 className="inline h-3.5 w-3.5" />
                </button>
              )}
            </div>
            {(historyLoadingId === row.id || historyErrorById[row.id] || historyById[row.id]) && (
              <div
                id={`material-history-${row.id}`}
                data-testid={`material-history-${row.id}`}
                className="col-span-full rounded-[6px] border border-[#D8E1EB] bg-[#F8FAFC] px-3 py-2 text-[11px] text-[#385A77]"
              >
                {historyLoadingId === row.id && <p role="status">正在读取版本链…</p>}
                {historyErrorById[row.id] && (
                  <div className="flex flex-wrap items-center gap-2 text-[#C62828]">
                    <span role="alert">{historyErrorById[row.id]}</span>
                    <button type="button" className="underline" onClick={() => loadHistory(row.id)}>重试</button>
                  </div>
                )}
                {historyById[row.id] && (
                  <>
                    {historyById[row.id].warnings?.length > 0 && (
                      <div
                        data-testid={`material-history-warnings-${row.id}`}
                        role="alert"
                        className="mb-1 rounded border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-1 text-[#92400E]"
                      >
                        版本链需人工核对：{historyById[row.id].warnings.map(historyWarningText).join('；')}
                      </div>
                    )}
                  <ol className="mt-1 flex flex-wrap items-center gap-2" aria-label="材料版本链">
                    {(historyById[row.id].chain || []).map((version, index) => (
                      <li key={version.material_id || version.id} className="inline-flex items-center gap-2">
                        <span className={version.version_role === 'superseded'
                          ? 'rounded border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-1 text-[#92400E]'
                          : 'rounded border border-[#CFE8D4] bg-[#F6FCF7] px-2 py-1 text-[#2E7D32]'}>
                          {version.file_name || `材料 ${version.material_id}`}
                          {version.version_role === 'superseded' ? ' · 已替代' : ' · 当前'}
                        </span>
                        {index < (historyById[row.id].chain || []).length - 1 && <span aria-hidden="true">→</span>}
                      </li>
                    ))}
                  </ol>
                  </>
                )}
              </div>
            )}
          </li>
          )})}
        </ul>
      {setupMode && onReplace && (
        // 替换入口的第二次点击（选文件）后立即带 replaces_material_id 上传；
        // 文件对话框由浏览器原生控件承担，前端不再追加确认弹窗。
        <input
          ref={replaceInputRef}
          type="file"
          hidden
          accept={accept}
          data-testid="material-replace-input"
          onChange={(e) => {
            const file = e.target.files?.[0] || null
            const targetId = replaceTargetRef.current
            replaceTargetRef.current = null
            e.target.value = ''
            if (file && targetId != null) onReplace(targetId, file)
          }}
        />
      )}
      {!hasFacts && (
        <p data-testid="material-facts-note" className={prototypeUpload ? 'sr-only' : 'border-t border-[#E8EEF4] px-4 py-2 text-[11px] text-[#94A3B8]'}>
          关键要素（合同段、签订日期、金额等）当前接口未返回，本台账只展示归类、主体与读取状态，不代填也不猜测。
        </p>
      )}
    </details>
  )
}
