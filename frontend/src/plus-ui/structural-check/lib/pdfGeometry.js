/**
 * PDF 预览几何计算（2026-09-25 T03：从 components/StructuralPdfPreview.jsx 抽出）。
 *
 * 本模块收纳 StructuralPdfPreview 里的**纯计算逻辑**：缩放钳制、页码钳制、
 * PDF 坐标矩形归一化、文本锚点归一化、行号解析、矩形合并、页码窗口构建、
 * 定位键构建、以及「PDF 矩形 → canvas 显示盒」的换算。
 *
 * 约束（与 lib/pageRange.js、lib/reviewState.js 一致）：
 *  - 纯函数：同入参恒同出参，无副作用；
 *  - 零依赖：不 import React / 不 import pdfjs / 不碰 DOM / 不读全局状态；
 *  - 行为与抽出前逐字等价（本次是重构，不是改需求）。
 *
 * 未抽出的部分及理由见交付报告：
 *  extractPageTextAnchors 直接调用 pdfjsLib.Util.transform（库级矩阵变换），
 *  抽入本模块会破坏「零依赖」契约，故留在组件内（其余纯逻辑已由本模块提供）。
 */

/** 缩放下界（与抽出前 StructuralPdfPreview.jsx 逐字一致）。 */
export const MIN_SCALE = 0.7
/** 缩放上界。 */
export const MAX_SCALE = 2
/** 单击一次缩放的步长。 */
export const SCALE_STEP = 0.15

/**
 * 把任意入参钳制为合法页码。
 *
 * 规则（复刻原实现）：parseInt 基数 10；非有限数 → 回落 1；再钳到 [1, max(pageCount,1)]。
 *
 * @param {*} value 目标页码（可为字符串/数字/NaN/负数）。
 * @param {number} pageCount 总页数（<=0 时按 1 处理下界）。
 * @returns {number} 合法页码，恒 >= 1。
 */
export function clampPage(value, pageCount) {
  const next = Number.parseInt(value, 10)
  if (!Number.isFinite(next)) return 1
  return Math.min(Math.max(next, 1), Math.max(pageCount, 1))
}

/**
 * 把缩放系数钳制到 [MIN_SCALE, MAX_SCALE]。
 *
 * 注意：与原实现一致，入参为 NaN 时返回 NaN（Math.max/min 对 NaN 的传播行为）。
 *
 * @param {number} value 目标缩放。
 * @param {number} [min] 下界，默认 MIN_SCALE。
 * @param {number} [max] 上界，默认 MAX_SCALE。
 * @returns {number} 钳制后的缩放。
 */
export function clampScale(value, min = MIN_SCALE, max = MAX_SCALE) {
  return Math.min(Math.max(value, min), max)
}

/**
 * 归一化后端给出的矩形列表（PDF 坐标）。
 *
 * 规则（复刻原实现）：非数组 → []；仅保留长度为 4 的条目；各分量 Number 化，
 * 非有限/0 → 0（`Number(x) || 0`）；丢弃非正面积（x1<=x0 或 y1<=y0）的矩形。
 *
 * @param {*} rects 形如 [[x0,y0,x1,y1], ...] 的矩形列表。
 * @returns {Array<{x0:number,y0:number,x1:number,y1:number}>} 归一化后的矩形。
 */
export function normalizeRectList(rects) {
  if (!Array.isArray(rects)) return []
  return rects
    .filter((rect) => Array.isArray(rect) && rect.length === 4)
    .map(([x0, y0, x1, y1]) => ({
      x0: Number(x0) || 0,
      y0: Number(y0) || 0,
      x1: Number(x1) || 0,
      y1: Number(y1) || 0,
    }))
    .filter((rect) => rect.x1 > rect.x0 && rect.y1 > rect.y0)
}

/**
 * 文本锚点归一化：NFKC + 去空白（含全角空格）+ 全角标点半角化 + trim。
 *
 * 用于把「后端 quote」与「PDF 抽出的行文本」拉到同一比较口径。
 * 备注：NFKC 本身已把全角标点/全角空格映射为半角；下方的显式 replace 链
 *       与抽出前逐字一致，保留以维持行为不变（其中标点 replace 为冗余但无害）。
 *
 * @param {*} text 原始文本。
 * @returns {string} 归一化文本（非字符串入参经 String() 处理，空/undefined → ''）。
 */
export function normalizeAnchorText(text) {
  return String(text || '')
    .normalize('NFKC')
    .replace(/[\r\n\t\s　]/g, '')
    .replace(/，/g, ',')
    .replace(/。/g, '.')
    .replace(/：/g, ':')
    .replace(/；/g, ';')
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .replace(/【/g, '[')
    .replace(/】/g, ']')
    .trim()
}

/**
 * 从 line_id（形如 `P3-L007`）解析行号。
 *
 * @param {*} lineId 行标识。
 * @returns {number} 行号；未匹配或解析为 0 → 0。
 */
export function parseLineNumber(lineId) {
  const matched = String(lineId || '').match(/-L(\d{1,})$/)
  if (!matched) return 0
  return Number(matched[1]) || 0
}

/**
 * 合并一组矩形为外接矩形。
 *
 * @param {Array<{x0:number,y0:number,x1:number,y1:number}>} rects 矩形列表。
 * @returns {{x0:number,y0:number,x1:number,y1:number}|null} 外接矩形；空列表 → null。
 */
export function mergeRects(rects) {
  if (!rects.length) return null
  const x0 = Math.min(...rects.map((rect) => rect.x0))
  const y0 = Math.min(...rects.map((rect) => rect.y0))
  const x1 = Math.max(...rects.map((rect) => rect.x1))
  const y1 = Math.max(...rects.map((rect) => rect.y1))
  return { x0, y0, x1, y1 }
}

/**
 * 构建「当前页 ± buffer」的渲染窗口（去重集合）。
 *
 * 规则（复刻原实现）：中心页先经 clampPage 钳制，再枚举 [center-buffer, center+buffer]，
 * 仅保留落在 [1, pageCount] 内的页。
 *
 * @param {*} centerPage 当前页。
 * @param {number} buffer 上下缓冲页数。
 * @param {number} pageCount 总页数。
 * @returns {Set<number>} 页码集合。
 */
export function buildPageWindow(centerPage, buffer, pageCount) {
  const next = new Set()
  const center = clampPage(centerPage, pageCount)
  for (let page = center - buffer; page <= center + buffer; page += 1) {
    if (page >= 1 && page <= pageCount) next.add(page)
  }
  return next
}

/**
 * 构建定位焦点键：用于判断「同一目标 finding 是否已处理/Applied」。
 *
 * 规则（复刻原实现）：非对象 → ''；否则以 '::' 拼接
 * [钳制后页码, 去空白 line_id, 去空白 quote, 归一化矩形串, focusKey]。
 *
 * @param {*} finding finding 对象。
 * @returns {string} 焦点键。
 */
export function buildFindingFocusKey(finding) {
  if (!finding || typeof finding !== 'object') return ''
  const page = clampPage(finding.page, Number.MAX_SAFE_INTEGER)
  const lineId = typeof finding.line_id === 'string' ? finding.line_id.trim() : ''
  const quote = typeof finding.quote === 'string' ? finding.quote.trim() : ''
  const rects = normalizeRectList(finding.rects)
    .map((rect) => `${rect.x0}:${rect.y0}:${rect.x1}:${rect.y1}`)
    .join('|')
  return [page, lineId, quote, rects, finding.focusKey || ''].join('::')
}

/**
 * 把单个 PDF 矩形换算为 canvas 显示盒（CSS px）。
 *
 * 换算（复刻原内联表达式）：
 *  left = x0 * scale，top = y0 * scale，
 *  width = (x1 - x0) * scale，height = (y1 - y0) * scale。
 *
 * @param {{x0:number,y0:number,x1:number,y1:number}} rect PDF 坐标矩形。
 * @param {number} scale 缩放系数。
 * @returns {{left:number,top:number,width:number,height:number}} 显示盒（数值，非字符串）。
 */
export function toDisplayRect(rect, scale) {
  return {
    left: rect.x0 * scale,
    top: rect.y0 * scale,
    width: (rect.x1 - rect.x0) * scale,
    height: (rect.y1 - rect.y0) * scale,
  }
}

/**
 * 从「可见比例表」中挑出主导页（占屏比例最大者）。
 *
 * 规则（复刻 IntersectionObserver 回调内的原实现）：忽略比例 <= 0 的页；
 * 在余下页中取比例最大者；并列时取先出现者（严格大于才替换）；无可见页 → null。
 *
 * @param {Map<number, number>|Array<[number, number]>} ratios 页号 → 可见比例。
 * @returns {number|null} 主导页号；无可见页 → null。
 */
export function pickDominantVisiblePage(ratios) {
  const entries = ratios instanceof Map
    ? [...ratios.entries()]
    : (Array.isArray(ratios) ? ratios : [])
  let bestPage = null
  let bestRatio = -1
  for (const entry of entries) {
    if (!Array.isArray(entry) || entry.length < 2) continue
    const page = Number(entry[0])
    const ratio = Number(entry[1]) || 0
    if (!(ratio > 0)) continue
    if (ratio > bestRatio) {
      bestRatio = ratio
      bestPage = page
    }
  }
  return bestPage
}

/**
 * 由文本锚点推导 finding 的矩形列表（当后端未给 rects 时的兜底定位）。
 *
 * 匹配优先级（复刻原实现）：
 *  1) line_id / lineNumber 精确命中 → 该行矩形；
 *  2) quote 单行包含（互相包含）→ 该行矩形；
 *  3) quote 跨行连续匹配 → 与命中区间**重叠**的连续行块矩形。
 *
 * @param {*} finding finding 对象（page / line_id / quote）。
 * @param {Array<{lineNumber:number,lineId:string,normalizedText:string,rect:object}>} pageAnchors 该页文本锚点。
 * @returns {Array<[number,number,number,number]>} 矩形数组（[x0,y0,x1,y1]）。
 */
export function deriveFindingRectsFromTextAnchors(finding, pageAnchors) {
  const page = clampPage(finding?.page, Number.MAX_SAFE_INTEGER)
  if (!page || !Array.isArray(pageAnchors) || !pageAnchors.length) return []

  const lineNumber = parseLineNumber(finding?.line_id)
  if (lineNumber > 0) {
    const matchedLine = pageAnchors.find((line) => line.lineNumber === lineNumber || line.lineId === finding.line_id)
    if (matchedLine?.rect) return [[matchedLine.rect.x0, matchedLine.rect.y0, matchedLine.rect.x1, matchedLine.rect.y1]]
  }

  const normalizedQuote = normalizeAnchorText(finding?.quote)
  if (!normalizedQuote) return []
  // 单行包含匹配（含 quote 被行包含或行被 quote 包含）
  const matchedSingle = pageAnchors.find((line) => (
    line.normalizedText.includes(normalizedQuote) || normalizedQuote.includes(line.normalizedText)
  ))
  if (matchedSingle?.rect) return [[matchedSingle.rect.x0, matchedSingle.rect.y0, matchedSingle.rect.x1, matchedSingle.rect.y1]]

  // P9：跨行连续匹配——quote 跨多行时，框定连续命中行块（替代原先只框单行导致内容漏框）
  // P32（2026-09-17）：只框与命中区间重叠的行——原实现把块首无关行（小节标题/图名等）
  // 一并框入，定位框视觉上覆盖无关区域。
  for (let start = 0; start < pageAnchors.length; start += 1) {
    let acc = ''
    const blockLines = []
    for (let end = start; end < pageAnchors.length; end += 1) {
      const line = pageAnchors[end]
      acc += line.normalizedText
      blockLines.push(line)
      if (acc.length >= normalizedQuote.length) {
        const hitIndex = acc.indexOf(normalizedQuote)
        if (hitIndex >= 0) {
          let cursor = 0
          const keptLines = []
          for (const blockLine of blockLines) {
            const spanStart = cursor
            const spanEnd = cursor + blockLine.normalizedText.length
            if (spanEnd > hitIndex && spanStart < hitIndex + normalizedQuote.length) keptLines.push(blockLine)
            cursor = spanEnd
          }
          return keptLines.map((bl) => [bl.rect.x0, bl.rect.y0, bl.rect.x1, bl.rect.y1])
        }
        break
      }
    }
  }
  return []
}