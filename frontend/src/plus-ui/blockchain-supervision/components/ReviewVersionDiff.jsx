import { useEffect, useMemo, useState } from 'react'
import { getBsVersionsDiff } from '../lib/api'
import { STATUS_WORD } from '../lib/statusDisplay'

// 刀B（BS-P2-02/03，设计 98 §5.2）：历史审查版本对比视图。
//
// 数据全部来自 `GET /tasks/{id}/versions/diff?a=&b=`（98 §5.1 合同）：
//   - 四数 counts：added / resolved / persisted / status_changed（互斥且覆盖两版并集）；
//   - items 四桶：新增 / 已解决 / 仍存在 / 状态变化；
//   - notes：后端写死的分母与口径说明，前端逐字展示，不重写措辞。
// 前端只做选择与呈现：不自行按 (item_id, material_id, page) 配对、不翻译三态、
// 不把 `resolved` 解释成「问题已解决」（合同 notes 已说明它可能是规则不再产出）。
//
// 默认选择按 98 §5.2「默认最新两版」：A=次新版本、B=最新版本；只有一版时
// B 退化为「当前结论」。用户改选后保留，只在所选版本消失时回退默认。

const BUCKETS = [
  ['added', '新增'],
  ['resolved', '已解决'],
  ['persisted', '仍存在'],
  ['status_changed', '状态变化'],
]

// 三态码 → 展示词（历史快照 / 旧后端兜底；后端 status_label 已归一化时优先用后端）。
function statusText(status) {
  if (status == null) return '—'
  return STATUS_WORD[status] || status
}

function sortedVersions(versions) {
  return [...(Array.isArray(versions) ? versions : [])]
    .filter((version) => version && version.version_no != null)
    .sort((a, b) => Number(a.version_no) - Number(b.version_no))
}

export function defaultDiffRefs(versions) {
  const list = sortedVersions(versions)
  if (list.length >= 2) {
    return {
      a: String(list[list.length - 2].version_no),
      b: String(list[list.length - 1].version_no),
    }
  }
  if (list.length === 1) return { a: String(list[0].version_no), b: 'current' }
  return { a: '', b: 'current' }
}

function bucketRows(bucket, truncatedMap, key) {
  // 合同 §5.1：`items` 是四桶数组，`items.truncated` 是「桶名 → 布尔」的旁表。
  // 若后端改为「{items, truncated}」包装也兼容读取。
  if (Array.isArray(bucket)) {
    return { rows: bucket, truncated: truncatedMap?.[key] === true }
  }
  return {
    rows: Array.isArray(bucket?.items) ? bucket.items : [],
    truncated: bucket?.truncated === true || truncatedMap?.[key] === true,
  }
}

function changedFieldsText(fields) {
  return Array.isArray(fields) && fields.length ? fields.join('、') : '无'
}

export default function ReviewVersionDiff({ taskId, versions = [] }) {
  const list = useMemo(() => sortedVersions(versions), [versions])
  const defaults = useMemo(() => defaultDiffRefs(list), [list])
  const [aRef, setARef] = useState(defaults.a)
  const [bRef, setBRef] = useState(defaults.b)
  const [diff, setDiff] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 版本列表变化（如刚重跑生成新快照）时，仅在当前选择已失效时回退默认。
  useEffect(() => {
    const validA = list.some((version) => String(version.version_no) === String(aRef))
    if (!validA) setARef(defaults.a)
    const validB = bRef === 'current' || list.some((version) => String(version.version_no) === String(bRef))
    if (!validB) setBRef(defaults.b)
  }, [list, defaults, aRef, bRef])

  if (!list.length) return null
  const disabled = loading || !aRef || !bRef
  const counts = diff?.counts || {}
  // 「两版一致」必须同时满足：无新增/已解决/状态变化，且「仍存在」条目字段也没变
  // （消息/依据/严重度变化虽然不改状态，但纸面结论变了，不能报成一致）。
  const persistedChanged = (diff?.items?.persisted || []).some(
    (row) => Array.isArray(row?.changed_fields) && row.changed_fields.length > 0,
  )
  const unchanged = diff != null
    && Number(counts.added || 0) === 0
    && Number(counts.resolved || 0) === 0
    && Number(counts.status_changed || 0) === 0
    && !persistedChanged

  const runDiff = async () => {
    if (!taskId || disabled) return
    setLoading(true)
    setError('')
    setDiff(null)
    try {
      setDiff(await getBsVersionsDiff(taskId, aRef, bRef))
    } catch (e) {
      // 读取失败必须显式报错；不得退化成「两版一致」的空态（会把故障说成结论）。
      setError(e?.message || '版本对比读取失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section data-testid="review-version-diff" className="mt-3 rounded-[6px] border border-[#EFE6D8] bg-[#FFFDF8] px-3 py-2.5">
      <div className="flex flex-wrap items-end gap-2">
        <h3 className="mr-auto text-[12px] font-semibold text-[#5B4A2F]">版本对比</h3>
        <label className="flex flex-col gap-0.5 text-[11px] text-[#6B7280]">
          基准版 A
          <select
            data-testid="diff-base-select"
            aria-label="基准版 A"
            value={aRef}
            disabled={loading}
            onChange={(e) => setARef(e.target.value)}
            className="rounded-[4px] border border-[#E5E5E5] bg-white px-2 py-1 text-[12px] text-[#4B5563] disabled:opacity-60"
          >
            {!aRef && <option value="">请选择版本</option>}
            {list.map((version) => (
              <option key={version.version_no} value={String(version.version_no)}>第 {version.version_no} 版</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-[11px] text-[#6B7280]">
          对比版 B
          <select
            data-testid="diff-target-select"
            aria-label="对比版 B"
            value={bRef}
            disabled={loading}
            onChange={(e) => setBRef(e.target.value)}
            className="rounded-[4px] border border-[#E5E5E5] bg-white px-2 py-1 text-[12px] text-[#4B5563] disabled:opacity-60"
          >
            {list.map((version) => (
              <option key={version.version_no} value={String(version.version_no)}>第 {version.version_no} 版</option>
            ))}
            <option value="current">当前结论</option>
          </select>
        </label>
        <button
          type="button"
          data-testid="diff-run"
          disabled={disabled}
          onClick={runDiff}
          className="min-h-8 rounded-[4px] border border-[#8B7355] bg-white px-3 text-[12px] text-[#8B7355] hover:bg-[#F7F1E9] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? '对比中…' : '对比'}
        </button>
      </div>

      {loading && <p data-testid="diff-loading" role="status" className="mt-2 text-[12px] text-[#6B7280]">正在读取两版逐项结论…</p>}
      {error && <p data-testid="diff-error" className="mt-2 rounded-[4px] border border-[#F5C6C6] bg-[#FFF7F7] px-2 py-1.5 text-[12px] leading-relaxed text-[#C62828]">对比失败：{error}</p>}

      {diff && (
        <div data-testid="diff-result" className="mt-3 space-y-2 border-t border-[#F3EDE3] pt-2">
          <p data-testid="diff-ruleset-versions" className="text-[11px] leading-relaxed text-[#6B7280]">
            规则集：A {diff.a?.ruleset_version || '未记录'} · B {diff.b?.ruleset_version || '未记录'}
            <span className="ml-2 text-[#9CA3AF]">
              （A 第 {diff.a?.version_no ?? '—'} 版{Number.isInteger(diff.a?.finding_count) ? ` · ${diff.a.finding_count} 条` : ''}
              ，B {diff.b?.ref === 'current' ? '当前结论' : `第 ${diff.b?.version_no ?? '—'} 版`}
              {Number.isInteger(diff.b?.finding_count) ? ` · ${diff.b.finding_count} 条` : ''}）
            </span>
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {BUCKETS.map(([key, label]) => (
              <div key={key} data-testid={`diff-count-${key}`} className="rounded-[4px] border border-[#EFE6D8] bg-white px-2.5 py-2">
                <span className="text-[11px] text-[#9CA3AF]">{label}</span>
                <b className="mt-0.5 block text-[16px] tabular-nums text-[#1A1A1A]">{Number(counts[key] || 0)}</b>
              </div>
            ))}
          </div>

          {unchanged ? (
            <p data-testid="diff-unchanged" className="rounded-[4px] border border-[#DCE8DD] bg-[#F3FAF3] px-2.5 py-2 text-[12px] leading-relaxed text-[#2E7D32]">
              两版逐项结论一致（仍存在 {Number(counts.persisted || 0)} 条）
            </p>
          ) : (
            <div className="space-y-2">
              {BUCKETS.map(([key, label]) => {
                const { rows, truncated } = bucketRows(diff.items?.[key], diff.items?.truncated, key)
                return (
                  <div key={key} data-testid={`diff-bucket-${key}`} className="rounded-[4px] border border-[#EFE6D8] bg-white px-2.5 py-2">
                    <h4 className="text-[11px] font-semibold text-[#5B4A2F]">
                      {label} <span className="tabular-nums text-[#9CA3AF]">{Number(counts[key] ?? rows.length)} 条</span>
                    </h4>
                    {rows.length === 0 ? (
                      <p data-testid={`diff-bucket-empty-${key}`} className="mt-1 text-[11px] text-[#9CA3AF]">无</p>
                    ) : (
                      <ul className="mt-1 space-y-1.5">
                        {rows.map((item, index) => (
                          <li
                            key={`${key}-${item.item_id || index}-${item.material_id ?? ''}-${item.page ?? ''}`}
                            data-testid={`diff-item-${key}-${item.item_id || index}`}
                            className="rounded-[3px] bg-[#FAFAF7] px-2 py-1.5 text-[11px] leading-relaxed text-[#4B5563]"
                          >
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              <b className="text-[#1A1A1A]">{item.item_id || '未标编号'}</b>
                              <span data-testid={`diff-item-status-${key}-${item.item_id || index}`} className="rounded border border-[#EFE6D8] bg-white px-1 py-0.5 text-[10px] text-[#6B7280]">
                                {statusText(item.status_a)} → {statusText(item.status_b)}
                              </span>
                              <span className="text-[#9CA3AF]">变化字段：{changedFieldsText(item.changed_fields)}</span>
                              {item.open_term && <span className="rounded border border-[#F0DCB8] bg-[#FFFDF8] px-1 py-0.5 text-[10px] text-[#92400E]">仅登记</span>}
                            </div>
                            <p className="mt-0.5 break-words">消息：{item.message_b ?? item.message_a ?? '—'}</p>
                            {item.message_a && item.message_b && item.message_a !== item.message_b && (
                              <p className="mt-0.5 break-words text-[#9CA3AF]">原消息：{item.message_a}</p>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    {truncated && <p className="mt-1 text-[10px] text-[#9CA3AF]">明细过多，仅显示前 500 条。</p>}
                  </div>
                )
              })}
            </div>
          )}

          {Array.isArray(diff.notes) && diff.notes.length > 0 && (
            <div data-testid="diff-notes" className="space-y-0.5 border-t border-[#F3EDE3] pt-1.5 text-[10px] leading-relaxed text-[#9CA3AF]">
              {diff.notes.map((note, index) => <p key={index}>· {note}</p>)}
            </div>
          )}
        </div>
      )}
    </section>
  )
}