// T10-C（材料台账与启动确认 V1）· 纯派生模块。
//
// 职责：把 get_task 返回的 `validation` 翻译成**启动前可读的准备状态**，
// 让用户在点「开始核查」之前就看见：哪些缺口阻止启动、哪些缺口只降低覆盖面、
// 哪些材料仍在读取。这里**只做分组与计数**，不新增、不改写任何后端判定：
// 缺口文案一律取后端平实 message（`service.validate_task_materials`），
// 前端不得根据文件名、关键词或颜色自行判断缺口类别。
//
// 后端 `validation` 结构（`service.validate_task_materials`）：
//   { hard_ok, hard_gaps[], soft_gaps[], composition{}, parsing_pending,
//     name_gaps[], failed_children[{material_id, file_name}] }
// - `hard_gaps`：阻止开始核查的缺口（文案已按口径写死）；
// - `soft_gaps`：允许继续、但会进入后续无法核实或降低覆盖面的缺口；
// - `parsing_pending`：仍有 uploading/parsing 材料，启动闸会 409；
// - `failed_children`：报审资料包内读取失败的子文件（其缺口已并入 soft_gaps）。

const PARSING_STATES = new Set(['uploading', 'parsing'])
const STORED_STATES = new Set(['stored'])

function messagesOf(list) {
  const out = []
  for (const item of list || []) {
    const text = typeof item === 'string' ? item.trim() : ''
    if (text) out.push(text)
  }
  return out
}

/**
 * @param {object|null} validation get_task.validation
 * @param {Array<object>} materials get_task.materials
 * @returns {{
 *   hard: string[], soft: string[], failedChildren: Array<object>,
 *   parsingPending: boolean, preparingPending: boolean, blocked: boolean,
 *   summary: { total: number, processing: number, preparing: number, hardCount: number, softCount: number }
 * }}
 */
export function buildStartupReadiness(validation, materials = []) {
  const v = validation || {}
  const list = Array.isArray(materials) ? materials : []
  const hard = messagesOf(v.hard_gaps)
  const soft = messagesOf(v.soft_gaps)
  // 只有后端明确说「有材料在读写」才算处理中；缺字段按未知处理，不猜成处理中。
  const parsingPending = v.parsing_pending === true
  // 容器停在 stored 不阻塞启动（准备阶段才展开），但台账要把它与「已读取」分开计数。
  const preparing = list.filter((m) => STORED_STATES.has(m?.parse_state)).length
  const processing = list.filter((m) => PARSING_STATES.has(m?.parse_state)).length
  const failedChildren = Array.isArray(v.failed_children) ? v.failed_children : []
  // 阻断判定只认后端结论（hard_ok===false / 存在硬缺口 / 解析未完成）；
  // validation 缺失时不伪造结论——交由调用方决定是否展示该区域。
  const blocked = parsingPending || v.hard_ok === false || hard.length > 0
  return {
    hard,
    soft,
    failedChildren,
    parsingPending,
    preparingPending: preparing > 0,
    blocked,
    summary: {
      total: list.length,
      processing,
      preparing,
      hardCount: hard.length,
      softCount: soft.length,
    },
  }
}