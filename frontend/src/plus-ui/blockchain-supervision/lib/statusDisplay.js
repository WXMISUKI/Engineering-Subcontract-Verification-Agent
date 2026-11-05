// 分项状态展示的唯一前端定义点（设计 100 §2.4 / BS-P1-15）。
//
// 纪律：
// - **状态字段值不变**（hit / miss / undecidable），这里只定义对外展示词；
// - 后端已下发 `status_label` 的地方一律直接用 `status_label`（见 statusLabelOf），
//   前端不再拼第二份词表；只有「没有 status_label 的本地场景」（聚合计数、文案模板、
//   历史快照缺字段）才用 STATUS_WORD 兜底；
// - 兜底条款（open_term）只登记、不构成结论，展示为「仅登记」，不得显示为「无法核实」，
//   也不计入 counts（BS-P1-11 语义不变）；
// - counts 键已由后端改为英文；本模块保留旧中文键双读回落，兼容旧后端 / 旧缓存 / 历史快照。
//
// 口径来源：docs/plus/blockchain-supervision/design/100-刀C规格与实现契约-v1.md §2。

/** 三态码（顺序即界面呈现顺序）。 */
export const STATUS_CODES = ['hit', 'miss', 'undecidable']

/** 三态 → 对外中文词（镜像自后端 models.STATUS_LABELS）。 */
export const STATUS_WORD = { hit: '不符合', miss: '符合', undecidable: '无法核实' }

/** 兜底条款（open_term=true）的展示词：只登记，不构成结论。 */
export const OPEN_TERM_WORD = '仅登记'

/** 用户主路径只消费两类结果；机器三态仍保留在 finding/report 事实中。 */
export const USER_OUTCOME_CODES = ['passed', 'pending']

/** 用户结果桶 → 主界面文案。 */
export const USER_OUTCOME_WORD = { passed: '通过', pending: '待处理' }

/** 旧 counts 中文键 → 英文键（历史快照 / 旧后端双读回落）。 */
export const LEGACY_COUNT_KEYS = { hit: '命中', miss: '未命中', undecidable: '不可判定' }

/**
 * 读三态计数：英文键优先，旧中文键回落。
 * 例：counts={miss:3} 与 counts={'未命中':3} 都返回 3；缺字段返回 0。
 * @param {Record<string, unknown>|null|undefined} counts
 * @param {'hit'|'miss'|'undecidable'} code
 * @returns {number}
 */
export function countOf(counts, code) {
  if (!counts || typeof counts !== 'object') return 0
  const legacy = LEGACY_COUNT_KEYS[code]
  const value = counts[code] ?? (legacy ? counts[legacy] : 0) ?? 0
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : 0
}

/**
 * finding / item 的展示状态词：
 * 1) 后端 `status_label` 原样优先（后端是唯一定义点）；
 * 2) 兜底条款无 label 时用 OPEN_TERM_WORD；
 * 3) 其余按三态码用 STATUS_WORD 兜底（历史快照 / 本地聚合）。
 * @param {object|null|undefined} finding
 * @returns {string}
 */
export function statusLabelOf(finding) {
  const label = typeof finding?.status_label === 'string' ? finding.status_label.trim() : ''
  if (label) return label
  if (finding?.open_term === true) return OPEN_TERM_WORD
  return STATUS_WORD[finding?.status] || ''
}

/** 三态码 → 展示词（不含 open_term 徽标优先级；仅用于模板拼接）。 */
export function statusWordOf(status) {
  return STATUS_WORD[status] || ''
}

/**
 * 机器三态 → 用户主路径两类结果。
 * - miss → passed（通过）
 * - hit / undecidable → pending（待处理）
 * - open_term / 未知状态 → null（不构成可处置结果）
 * @param {object|string|null|undefined} findingOrStatus
 * @returns {'passed'|'pending'|null}
 */
export function userOutcomeOf(findingOrStatus) {
  if (findingOrStatus && typeof findingOrStatus === 'object') {
    if (findingOrStatus.open_term === true) return null
    return userOutcomeOf(findingOrStatus.status)
  }
  if (findingOrStatus === 'miss') return 'passed'
  if (findingOrStatus === 'hit' || findingOrStatus === 'undecidable') return 'pending'
  return null
}
