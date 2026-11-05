// T10-C（ZIP 与主体确认前端接线）· 纯派生模块。
//
// 职责：把后端 DTO 里「材料/容器状态」与「主体类型确认需求」翻译成**用户可见文案与
// 判定**。放在这里而不是入口组件里，是为了：① 入口不重复派生（R1A 已有纪律）；
// ② 文案与判定可以被独立单测钉死，不必挂整棵生产入口。
//
// 口径来源（后端冻结，见 T10-B 交付回报）：
//   - 材料/容器 `parse_state`：`uploaded` / `parsing` / `stored` / `done` / `failed`
//     （`stored` 是**容器专用**取值：原包已保存、待准备阶段展开）。
//   - 任务态 `state`：`setup` / `preparing` / `running` / `review` / `done`。
//   - 容器身份在库里无专属列，前端按**两路并集**判定：`zip_preparation.containers[]`
//     声明的容器 ID，以及「被子材料指向的父材料 ID」。
//   - 主体 `subject_type`：`professional` / `labor`（A-03 之前的历史数据或夹具可能是
//     `sub`，一并容忍映射，避免把历史值显示成空白）。

/** 后端冻结的主体类型取值 → 用户可见文案（历史 `sub` 一并容忍）。 */
export const SUBJECT_TYPE_LABELS = {
  professional: '专业分包',
  sub: '专业分包',
  labor: '劳务分包',
}

export function subjectTypeLabel(t) {
  return SUBJECT_TYPE_LABELS[t] || null
}

// 后端 `stable_code` 是**任务内身份编号**，创建时按当时的类型取前缀、之后永不改写
// （类型变更走 `set_subject_type`，编号不变）。因此前缀里的「专业分包/劳务分包」
// 是历史前缀，不是当前类型声明 —— 两者并排显示会出现「专业分包1 · 劳务分包」这种
// 自相矛盾的组合。展示层把前缀剥掉只留序号，类型由 `subject_type` 单独渲染。
const TYPED_STABLE_CODE_RE = /^(专业分包|劳务分包|分包)(\d+)$/

/** 主体编号的身份写法：`专业分包1` → `主体 1`；非该形态的编号原样返回。 */
export function subjectCodeLabel(stableCode) {
  const code = typeof stableCode === 'string' ? stableCode.trim() : ''
  if (!code) return ''
  const match = TYPED_STABLE_CODE_RE.exec(code)
  return match ? `主体 ${match[2]}` : code
}

/** 主体的展示名：有名称用名称，否则用身份编号（不含类型前缀）。 */
export function subjectDisplayName(subject) {
  const name = typeof subject?.name === 'string' ? subject.name.trim() : ''
  return name || subjectCodeLabel(subject?.stable_code)
}

/** 主体名称状态由后端统一计算；未知值按需确认处理，避免静默使用 OCR 名称。 */
export function subjectNeedsNameConfirm(subject) {
  const status = subject?.name_status
  if (status) return status === 'missing' || status === 'low_confidence'
  return !subject?.name || subject?.origin === 'material_auto'
}

export function subjectNameWarning(subject) {
  if (subject?.name_warning) return subject.name_warning
  if (subjectNeedsNameConfirm(subject)) return '请先确认分包单位名称'
  return ''
}

export function subjectsNeedingNameConfirm(subjects) {
  return (subjects || []).filter(subjectNeedsNameConfirm)
}

/**
 * 容器 ID 集合（两路并集）。
 * - `zipContainerIds`：后端 `zip_preparation.containers[].container_material_id`（权威声明）。
 * - 另加「被任一材料指向的父材料 ID」——覆盖 zip_preparation 尚未生成时的中间态。
 */
export function collectContainerIds(materials, zipContainerIds) {
  const ids = new Set()
  for (const id of zipContainerIds || []) if (id != null) ids.add(id)
  for (const m of materials || []) if (m && m.container_material_id != null) ids.add(m.container_material_id)
  return ids
}

/**
 * 材料的**用户可见状态**（单一表源，互斥）。
 *
 * 返回 `{ kind, label, tone }`，`kind` 是稳定标识供测试断言，`label` 才是文案。
 * 判定优先级：失败 > 待准备/准备中 > 就绪 > 进行中。
 *
 * ⚠️ `stored` **绝不可**显示成「解析完成」——容器此时尚未展开，内容一个字都还没读
 * （契约 §7.2.4）。任务处于 `preparing` 时它显示「资料准备中」，否则显示「待准备」。
 */
export function describeMaterialState(material, { taskState, containerIds } = {}) {
  const m = material || {}
  const ps = m.parse_state
  const isContainer = containerIds ? containerIds.has(m.id) : ps === 'stored'
  const preparing = taskState === 'preparing'

  if (ps === 'failed') {
    return isContainer
      ? { kind: 'failed', label: '准备失败', tone: 'bad' }
      : { kind: 'failed', label: '处理失败', tone: 'bad' }
  }
  if (ps === 'stored') {
    return preparing
      ? { kind: 'preparing', label: '资料准备中', tone: 'warn' }
      : { kind: 'stored', label: '待准备', tone: 'muted' }
  }
  // 准备期间子材料可能仍停在 uploaded/parsing —— 对用户一律是「资料准备中」，
  // 不能让 ZIP 用户看到一列「上传中」（那不是他能操作的东西）。
  if (preparing && (ps === 'uploaded' || ps === 'parsing')) {
    return { kind: 'preparing', label: '资料准备中', tone: 'warn' }
  }
  if (ps === 'done') {
    return isContainer
      ? { kind: 'done', label: '准备完成', tone: 'ok' }
      : { kind: 'done', label: '已准备好', tone: 'ok' }
  }
  if (ps === 'parsing') return { kind: 'parsing', label: '处理中', tone: 'muted' }
  return { kind: 'uploaded', label: '已上传', tone: 'muted' }
}

/** 绝对路径特征（Windows 盘符 / POSIX 根）——用于确保**绝不**把服务器路径渲染给用户。 */
const ABS_PATH_RE = /^([A-Za-z]:[\\/]|\\\\|\/)/

/**
 * 派生材料的**来源路径**：`容器文件名 / ZIP 内相对路径`（契约 §7.4.3）。
 * 普通 PDF 与历史材料返回 null（不回填伪来源）。
 * 若后端意外给出绝对路径形态，返回 null 而不是把它渲染出去（不泄露服务器路径）。
 */
export function materialSourcePath(material) {
  const m = material || {}
  if (m.container_material_id == null) return null
  const container = typeof m.container_file_name === 'string' ? m.container_file_name.trim() : ''
  const entry = typeof m.container_entry_path === 'string' ? m.container_entry_path.trim() : ''
  if (entry && ABS_PATH_RE.test(entry)) return null
  if (container && ABS_PATH_RE.test(container)) return null
  if (!container && !entry) return null
  return `${container || '资料包'} / ${entry || ''}`.trim()
}

/**
 * 确认弹窗的「材料就绪度」文案。
 *
 * **优先级（T10-C-R1 冻结，不得再调换）**：
 *   1) 任务级 `preparing` —— 用户已经点了开始核查、系统正在准备资料；此时容器可能仍停在
 *      `stored`（准备 worker 尚未展开）。说「待准备」与真实任务状态矛盾，所以
 *      **任务级事实恒高于材料级事实**。
 *   2) 材料仍在解析/上传（`parsing` / `uploaded`）。
 *   3) 有资料包待准备（`stored`）—— 该文案只在任务仍为 `setup` 时才成立。
 *   4) 其余（材料已收口）。
 *
 * ⚠️ 只要还有 `stored`（待准备）或 `parsing/uploaded`，就**不得**说「解析全部完成」——
 * 那会让用户以为资料包已经读完了，实际它一个字节都还没展开。
 */
export function materialReadinessSummary(materials, { taskState } = {}) {
  const list = materials || []
  if (!list.length) return ''
  // T10-C-R1 Task R1-1：任务级 `preparing` 提到最前，覆盖容器级 `stored` 文案。
  if (taskState === 'preparing') return '（资料包准备中）'
  const pendingParse = list.some((m) => m.parse_state === 'parsing')
  const pendingPrepare = list.some((m) => m.parse_state === 'stored')
  if (pendingParse) return '（有材料仍在处理）'
  if (pendingPrepare) return '（有资料包待准备，开始核查后将自动展开读取）'
  if (list.some((m) => m.parse_state === 'uploaded')) return '（开始核查后读取材料）'
  return '（材料已全部读取）'
}

/**
 * 主体是否需要用户做一次类型确认（§C4）。
 *
 * 类型以上传时所选资料类别为准（方案A），AI 证据分歧（type_conflict）只在台账
 * 里提示、不要求用户再点一次。只有两种情况需要确认：类型**从未确认**、类型值不可识别。
 */
export function subjectNeedsTypeConfirm(subject) {
  const s = subject || {}
  if (!s.type_confirmed) return true
  if (!subjectTypeLabel(s.subject_type)) return true
  return false
}

/** 需要确认的主体列表（用于「回 setup 后定位到问题主体」与缺口横幅计数）。 */
export function subjectsNeedingConfirm(subjects) {
  return (subjects || []).filter(subjectNeedsTypeConfirm)
}

/**
 * 准备失败/回退的可读原因（只取后端平实 message，不暴露技术栈）。
 * 输入是详情里的 `zip_preparation`。
 */
export function zipPreparationFailureMessages(zipPreparation) {
  const z = zipPreparation || {}
  if (z.state !== 'failed') return []
  const out = []
  for (const c of z.containers || []) {
    const msg = (c && typeof c.message === 'string' && c.message.trim()) ? c.message.trim() : ''
    out.push(msg || `资料包「${c && c.file_name ? c.file_name : '未命名'}」无法展开，请移除后重新上传`)
  }
  if (!out.length) out.push('报审资料包无法准备，请移除或重新上传后再开始核查')
  return out
}
