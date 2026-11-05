// 刀D（设计 104 §4.2）：展示层文本消毒——把入库文本里混入的 HTML 标记/实体清掉。
//
// 背景：真实材料的 PDF 文本层可能带 `<p>`、`<h2>` 这类标记（截图证据见设计 104 §1），
// 前端原样渲染时用户会看到标签。纪律：
//   - 只用于**展示**，不改库、不改判定、不改报告内容；
//   - 只剥 ASCII 标签（`<p>`、`</p>`、`<!-- -->`）；`a < b`、`<3%`、`<见>` 不得误删；
//   - 实体先解码再剥标签，最多两轮（兼容 `&amp;lt;p&amp;gt;` 这种二次转义）。

const NAMED_ENTITIES = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
}

function fromCodePoint(raw, radix) {
  const code = Number.parseInt(raw, radix)
  if (!Number.isFinite(code) || code < 0 || code > 0x10FFFF) return ''
  try {
    return String.fromCodePoint(code)
  } catch {
    return ''
  }
}

/** 解码 HTML 实体（命名实体 + 十进制/十六进制数字实体），最多两轮。 */
export function decodeEntities(value) {
  if (typeof value !== 'string' || value.length === 0) return typeof value === 'string' ? value : ''
  let text = value
  for (let round = 0; round < 2; round += 1) {
    const next = text
      .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => fromCodePoint(hex, 16))
      .replace(/&#(\d+);/g, (_, dec) => fromCodePoint(dec, 10))
      .replace(/&(amp|lt|gt|quot|apos|nbsp);/g, (_, name) => NAMED_ENTITIES[name])
    if (next === text) break
    text = next
  }
  return text
}

/**
 * 清理 OCR/PDF 文本层偶尔泄漏的 LaTeX 片段。
 * 只在检测到 TeX 命令时启用，避免把正常金额中的 `$` 当作标记删除。
 */
export function stripLatexMarkup(value) {
  if (typeof value !== 'string' || !/\\(?:text|mathrm|mathbf|textbf|textit)\s*\{|\$\s*\\/.test(value)) {
    return value
  }
  let text = value
  for (let round = 0; round < 2; round += 1) {
    const next = text.replace(/\\(?:text|mathrm|mathbf|textbf|textit)\s*\{([^{}]*)\}/g, '$1')
    if (next === text) break
    text = next
  }
  return text
    .replace(/\\(?:text|mathrm|mathbf|textbf|textit)\b/g, '')
    .replace(/\\(?:,|;|!|quad|qquad|cdot)\b/g, ' ')
    .replace(/\\\\/g, ' ')
    .replace(/[${}]/g, ' ')
}

/**
 * 剥离标记并压平空白；非字符串一律返回 ''（展示函数不做类型转换的猜测）。
 * 标签只匹配 ASCII 标签名，因此中文比较符/小于号的正文不会被吃掉。
 */
export function stripMarkup(value) {
  if (typeof value !== 'string') return ''
  const decoded = stripLatexMarkup(decodeEntities(value))
  return decoded
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<\/?[a-zA-Z][^<>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 展示层文本入口：字符串走消毒，null/undefined 保持原样（不把“没有”变成空串）。 */
export function cleanText(value) {
  if (value == null) return null
  if (typeof value !== 'string') return value
  return stripMarkup(value)
}
