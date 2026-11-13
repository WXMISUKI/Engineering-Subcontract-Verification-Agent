const BASE = '/api/plus/blockchain-supervision'
// 同一挂载窗口内平台壳层与 BS 工作台可能同时探活；健康探针是只读幂等请求，
// 合并并发请求即可避免重复占用连接，不缓存结果，也不影响带 signal 的调用方。
let pingRequest = null

/** 只有非空字符串才算是可用文案（空串 / 纯空白 / 其他类型一律当作「没有」）。 */
function _text(v) {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/**
 * T10-A 结构化错误可读化：把后端 `detail` 翻译成一句可读中文。
 *
 * 背景：后端启动闸/收口/重核查闸的 `detail` 是对象（service.py 启动闸返回
 * `{message, gaps}`），旧实现在 `_json`/`_blob` 里直接 `new Error(body.detail)`
 * → 界面出现 `[object Object]`，违反「护栏必须可理解」。
 *
 * 判定顺序（与任务书 §二 一一对应）：
 *   1. `detail` 为非空字符串            → 直接使用
 *   2. `detail.message` 为非空字符串    → 优先使用（gaps 是逐条明细，界面不全量渲染）
 *   3. `detail.gaps` 存在但无 message   → 用可读的 `gaps[].message` 拼成一句
 *   4. 其他对象 / 数组 / null / 空正文 / 非 JSON → 返回 null，由调用方退平实兜底
 *
 * 返回 null 表示「无可读文案」；绝不返回对象或 JSON 技术串。
 */
function _readableDetail(detail) {
  const direct = _text(detail)
  if (direct) return direct
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = _text(detail.message)
    if (message) return message
    if (Array.isArray(detail.gaps)) {
      const parts = detail.gaps.map((g) => _text(g && g.message)).filter(Boolean)
      if (parts.length) return parts.join('；')
    }
    // 补件接口返回 {ok, reason, detail}，业务原因在内层 detail。
    if (detail.ok === false && detail.detail !== detail) {
      return _readableDetail(detail.detail)
    }
  }
  return null
}

/**
 * 由响应 body/tag/status 造 Error：`message` 恒为非空可读字符串；
 * 结构化信息保留在 `err.detail`（原始 `detail`，调用方需要时可消费；
 * 界面不得直接渲染对象本身），HTTP 状态保留在 `err.status`（调用方据此把 409
 * 覆盖提示与网络故障分开呈现，不得把覆盖提示说成「网络错误」）。
 * 不改 HTTP 状态、请求方法、URL、凭据或 abort signal。
 */
function _httpError(body, tag, status) {
  const detail = body && typeof body === 'object' ? body.detail : undefined
  if (status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('bs:auth-expired'))
  }
  const fallback = status === 502
    ? `${tag}暂时不可用（网关 502），后台服务可能正在重启或上游模型暂时不可用，请稍后重试`
    : `${tag} ${status}`
  const err = new Error(_readableDetail(detail) || fallback)
  if (detail !== undefined) err.detail = detail
  err.status = status
  return err
}

async function _json(r, tag) {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw _httpError(body, tag, r.status)
  }
  return r.json()
}

// 材料修改/补件属于用户可感知的写操作，不能让弹窗永久停留在“处理中”。
// 后端摄取本身是延迟解析，上传请求应在保存回执后结束；超过该时限说明
// 服务不可达或请求卡住，调用方需要恢复按钮并展示可重试的原因。
const MATERIAL_MUTATION_TIMEOUT_MS = 30_000

async function _fetchMaterialMutation(url, init, tag) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), MATERIAL_MUTATION_TIMEOUT_MS)
  let timeoutId
  try {
    const response = await Promise.race([
      fetch(url, { ...init, signal: controller.signal }),
      new Promise((_, reject) => {
        const timeoutError = new Error(`${tag}请求超时，请检查后台服务后重试`)
        timeoutError.code = 'BS_MUTATION_TIMEOUT'
        timeoutId = setTimeout(() => reject(timeoutError), MATERIAL_MUTATION_TIMEOUT_MS)
      }),
    ])
    return response
  } catch (error) {
    if (error?.name === 'AbortError') {
      const timeoutError = new Error(`${tag}请求超时，请检查后台服务后重试`)
      timeoutError.code = 'BS_MUTATION_TIMEOUT'
      throw timeoutError
    }
    throw error
  } finally {
    clearTimeout(timer)
    clearTimeout(timeoutId)
  }
}

export async function listBsTasks(conversationId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks?conversation_id=${conversationId}`, { credentials: 'include', signal })
  return _json(r, 'bs tasks')
}

// A-10 健康探针（B0）：GET /ping——轻量存活探测，用于工作台自检/运维联通判断。
export async function pingBs({ signal } = {}) {
  if (!signal && pingRequest) return pingRequest
  const request = fetch(`${BASE}/ping`, { credentials: 'include', signal })
    .then((r) => _json(r, 'bs ping'))
  if (signal) return request
  pingRequest = request.finally(() => {
    pingRequest = null
  })
  return pingRequest
}

export async function createBsTask({ conversationId, title, scopeA1, scopeA3, projectName, contractSection, baselineDate }) {
  const body = {
    conversation_id: conversationId, title, scope_a1: scopeA1, scope_a3: scopeA3,
    // A-03（B1）标段锚点：可选三字段（未填写则留空，后端存 NULL）
    project_name: projectName || null,
    contract_section: contractSection || null,
    baseline_date: baselineDate || null,
  }
  const r = await fetch(`${BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
  })
  return _json(r, 'bs create')
}

export async function getBsTask(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}`, { credentials: 'include', signal })
  return _json(r, 'bs task')
}

// 任务级计时与 Token 用量只读视图；shadow 阶段只展示真实用量，不扣余额。
export async function getBsTaskUsage(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/usage`, { credentials: 'include', signal })
  return _json(r, 'bs task usage')
}

// D9/T2-A3：材料替代历史只读查询。版本角色、修订号和链路顺序均由后端返回，
// 前端不根据当前列表重新拼链，也不把历史行改写为新的业务状态。
export async function getBsMaterialHistory(taskId, materialId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/materials/${materialId}/history`, {
    credentials: 'include', signal,
  })
  return _json(r, 'bs material history')
}

export async function listBsReviewVersions(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/versions`, { credentials: 'include', signal })
  return _json(r, 'bs review versions')
}

export async function getBsReviewVersion(taskId, versionNo, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/versions/${versionNo}`, {
    credentials: 'include', signal,
  })
  return _json(r, 'bs review version')
}

// 刀B（BS-P2-04）：规则集版本只读视图（GET /tasks/{id}/ruleset）。
// 返回体形状见设计 98 §3.3：current_version / task_version / changed /
// declared_changed_item_ids / files / note。前端只展示，不自行比对文件哈希或
// 猜测版本差异；读取失败必须显式失败（界面显示「规则集信息暂不可用」），
// 不得在失败时伪造「无变化」或 0 条变化。
export async function getBsRuleset(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/ruleset`, { credentials: 'include', signal })
  return _json(r, 'bs ruleset')
}

// 刀B（BS-P2-02）：版本差异（GET /tasks/{id}/versions/diff?a=&b=）。
// ref 为版本号整数或字面量 "current"（当前库内结论）；四数、四桶明细、notes
// 全部由后端按设计 98 §5.1 计算，前端不重新配对、不改桶定义。
export async function getBsVersionsDiff(taskId, a, b, { signal } = {}) {
  const query = `a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`
  const r = await fetch(`${BASE}/tasks/${taskId}/versions/diff?${query}`, {
    credentials: 'include', signal,
  })
  return _json(r, 'bs versions diff')
}

// T8-U5-R1A Task 2：核查结论列表读取（GET /tasks/{id}/findings）。
// 必须走 _json —— 非 2xx 时抛出后端 detail（缺失则退平实 fallback：`bs findings <status>`），
// 使「请求失败」在调用方成为**显式失败**，不再被裸 fetch + r.json() 伪装成「暂无核查结论」。
export async function listBsFindings(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/findings`, { credentials: 'include', signal })
  return _json(r, 'bs findings')
}

export async function getBsGroupAiStatus(taskId, groupId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/check/${encodeURIComponent(groupId)}/ai-advisory-batch`, { credentials: 'include' })
  return _json(r, 'AI 核查状态')
}

export async function startBsBackgroundAiReview(taskId, reviewRunId, { retry = false } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/ai-review`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_run_id: reviewRunId, retry }),
  })
  return _json(r, '后台 AI 核查')
}

export async function getBsBackgroundAiReview(taskId, reviewRunId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/ai-review?review_run_id=${encodeURIComponent(reviewRunId)}`, { credentials: 'include' })
  return _json(r, '后台 AI 核查状态')
}

export async function runBsGroupAiAdvisory(taskId, groupId, runId, { resumePartial = false } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/check/${encodeURIComponent(groupId)}/ai-advisory-batch`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, resume_partial: resumePartial }),
  })
  return _json(r, 'AI 分批核查')
}

// 刀A（BS-P1-06）：待办聚合读取（GET /tasks/{id}/todo）。
// 缺件按材料类归并、是否可补件（actionable）与计数全部由后端给出（reporting.missing_summary），
// 前端只展示，不自行聚合缺件、不从 message 拆文案。非 2xx 必须显式失败：
// 面板要能说「待补材料暂不可用」，不能把读取失败伪装成「没有缺件」。
export async function getBsTodo(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/todo`, { credentials: 'include', signal })
  return _json(r, 'bs todo')
}

export async function confirmBsMaterial(taskId, findingId, decision, token, expectedConfirmationId = null, materialId = null) {
  const r = await fetch(`${BASE}/tasks/${taskId}/material-confirmations/${findingId}`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, token, expected_confirmation_id: expectedConfirmationId, material_id: materialId }),
  })
  return _json(r, '材料确认')
}

export async function confirmBsEmployer(taskId, candidate) {
  const r = await fetch(`${BASE}/tasks/${taskId}/employer-confirmation`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ material_id: candidate.material_id, page: candidate.page, value: candidate.value }),
  })
  return _json(r, 'bs employer confirmation')
}

// A-10 删除任务（B0）：DELETE /tasks/{id}——任务与三张子表一并删除（后端显式删）。
// setup 阶段「放弃本次核查」与工作台「清理任务」的入口。
export async function deleteBsTask(taskId) {
  const r = await fetch(`${BASE}/tasks/${taskId}`, { method: 'DELETE', credentials: 'include' })
  return _json(r, 'bs delete task')
}

// A-10 触发词召回观察数据（B0）：GET /observation-stats——按核查项聚合三态计数与
// 无法核实占比（只读，按当前用户过滤）。供决策「是否接 LLM 语义通道」。
export async function getBsObservationStats({ signal } = {}) {
  const r = await fetch(`${BASE}/observation-stats`, { credentials: 'include', signal })
  return _json(r, 'bs observation stats')
}

export async function patchBsScope(taskId, scopeA1, scopeA3) {
  const r = await fetch(`${BASE}/tasks/${taskId}/scope`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope_a1: scopeA1, scope_a3: scopeA3 }),
    credentials: 'include',
  })
  return _json(r, 'bs scope')
}

// A-03（B1）标段锚点（setup 段可改，开始核查后 409）：PATCH /tasks/{id}/section。
// 与 patchBsScope 同构——只发要改的字段（PATCH 粒度语义由后端 model_fields_set 承载）。
export async function patchBsSection(taskId, fields = {}) {
  const body = {}
  if ('projectName' in fields) body.project_name = fields.projectName || null
  if ('contractSection' in fields) body.contract_section = fields.contractSection || null
  if ('baselineDate' in fields) body.baseline_date = fields.baselineDate || null
  const r = await fetch(`${BASE}/tasks/${taskId}/section`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
  })
  return _json(r, 'bs section')
}

export async function uploadBsMaterial(taskId, file, { role, subjectId, packetId, materialCategory, replacesMaterialId, supplementGenerationId } = {}) {
  const fd = new FormData()
  fd.append('file', file)
  // 后端逐步接入主体中心模型；未知字段在旧后端会被忽略，保持上传兼容。
  if (role) fd.append('role', role)
  if (subjectId != null) fd.append('subject_id', String(subjectId))
  if (packetId != null) fd.append('packet_id', String(packetId))
  if (materialCategory) fd.append('material_category', materialCategory)
  if (supplementGenerationId) fd.append('supplement_generation_id', supplementGenerationId)
  // 刀B（BS-P2-01）：显式替换上传——表单字段名按设计 98 §6.1 为 replaces_material_id。
  // 只有调用方明确指定要替换哪份材料时才发送；后端校验归属/是否已被替换，
  // 前端不做文件名或类别的启发式推断。
  if (replacesMaterialId != null) fd.append('replaces_material_id', String(replacesMaterialId))
  const r = await _fetchMaterialMutation(`${BASE}/tasks/${taskId}/materials/upload`, {
    method: 'POST', body: fd, credentials: 'include',
  }, '材料上传')
  return _json(r, 'bs upload')
}

// A-10 登记式加材料（B0）：POST /tasks/{task_id}/materials（JSON，非文件上传）——
// 台账/外部系统已带入的材料只登记名称与归属，不落盘。role 缺省走后端自动归类。
export async function addBsMaterial(taskId, { fileName, sizeKb = 0, via = 'ledger', role = null }) {
  const body = { file_name: fileName, size_kb: sizeKb, via }
  if (role) body.role = role
  const r = await fetch(`${BASE}/tasks/${taskId}/materials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
  })
  return _json(r, 'bs add material')
}

export async function setBsMaterialRole(taskId, materialId, role) {
  const r = await fetch(`${BASE}/tasks/${taskId}/materials/${materialId}/role`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
    credentials: 'include',
  })
  return _json(r, 'bs role')
}

// setup 阶段为已上传的报审资料包补选/更换关联分包主体。
// 后端使用同一条材料归属接口，避免上传前后出现两套主体绑定逻辑。
export async function assignBsMaterialSubject(taskId, materialId, subjectId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/materials/${materialId}/subject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject_id: Number(subjectId) }),
    credentials: 'include',
  })
  return _json(r, 'bs material subject')
}

export async function associateBsGeneralSourceSet(taskId, materialIds) {
  const r = await fetch(`${BASE}/tasks/${taskId}/general-source-sets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ material_ids: materialIds }),
    credentials: 'include',
  })
  return _json(r, 'bs general source set')
}

export async function removeBsGeneralSourceSetMember(taskId, materialId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/materials/${materialId}/general-source-set`, {
    method: 'DELETE', credentials: 'include',
  })
  return _json(r, 'bs general source set member')
}

// T10-C Task C1：人工确认/覆盖主体类型（契约 v3 §2.7，**冲突的唯一解除入口**）。
// PATCH /tasks/{task_id}/subjects/{subject_id}/type，body { subject_type }；
// 后端返回 _subject_dto（subject_id/stable_code/name/subject_type/type_confirmed/
// type_locked/ai_subject_type/type_conflict/type_conflict_reason）。
// 与其余 helper 同构：走 _json，非 2xx 抛后端可读 detail（T10-A 统一错误契约），
// 不在此处复制任何错误解析逻辑。404=主体不存在 / 409=已锁定或非 setup 段。
export async function confirmBsSubjectType(taskId, subjectId, subjectType) {
  const r = await fetch(`${BASE}/tasks/${taskId}/subjects/${subjectId}/type`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject_type: subjectType }),
    credentials: 'include',
  })
  return _json(r, 'bs subject type')
}

// setup 阶段确认或修改 OCR 自动抽取的主体名称；后端会写入审计事件并提升为人工确认。
export async function renameBsSubject(taskId, subjectId, name) {
  const r = await fetch(`${BASE}/tasks/${taskId}/subjects/${subjectId}/name`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
    credentials: 'include',
  })
  return _json(r, 'bs subject name')
}

export async function deleteBsMaterial(taskId, materialId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/materials/${materialId}`, {
    method: 'DELETE', credentials: 'include',
  })
  return _json(r, 'bs delete material')
}

export async function startBsReview(taskId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/start`, { method: 'POST', credentials: 'include' })
  return _json(r, 'bs start')
}

export async function runBsCheck(taskId, kind, { signal } = {}) {
  const path = {
    completeness: 'check',
    a1: 'check/a1',
    signature: 'check/a3-signature',
    signature_chain: 'check/a3-signature',
    a2: 'check/a2',
    internal_records: 'check/internal-records',
    semantic: 'check/semantic',
  }[kind]
  if (!path) throw new Error(`未知核查组：${kind || '未提供'}`)
  const r = await fetch(`${BASE}/tasks/${taskId}/${path}`, { method: 'POST', credentials: 'include', signal })
  return _json(r, `bs check ${kind}`)
}

// 收口任务（P0-2）：范围要求的全部分组均有结论才允许 → state=done + 发射 task.completed。
// 条件不满足时后端返回 409 + detail（缺哪些分组），由 _json 抛出后交给界面 onError 呈现；
// 已 done 态重复收口幂等返回。
export async function completeBsTask(taskId) {
  const r = await fetch(`${BASE}/tasks/${taskId}/complete`, { method: 'POST', credentials: 'include' })
  return _json(r, 'bs complete')
}

// 任务内重核查（A-02）：作废旧结论 + 免重解析 → state 回 running + 发射 task.rerun。
// 解析中 / setup 段后端返回 409 + detail，由 _json 抛出后交给界面 onError 呈现。
//
// 刀B（BS-P1-13）扩展：可选 `{ scope: 'items', itemIds: [...] }`（设计 98 §4.1）。
// - 不传参数时**保持旧行为**：POST 无请求体（全量重跑），旧调用方与旧后端兼容；
// - scope=items 只携带 item_ids，由后端在第 2 步才快照旧版本、按范围过滤落库；
// - 前端不校验 item_id 长度/去重（那是后端 400 闸），也不在此处猜哪些条目受影响。
export async function rerunBsTask(taskId, { scope, itemIds } = {}) {
  const init = { method: 'POST', credentials: 'include' }
  if (scope != null || itemIds != null) {
    const body = {}
    if (scope != null) body.scope = scope
    if (itemIds != null) body.item_ids = itemIds
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  const r = await fetch(`${BASE}/tasks/${taskId}/rerun`, init)
  return _json(r, 'bs rerun')
}

// P8-03C：先登记补件局部核查计划，后续阶段再显式启动代际调度。
export async function createBsSupplementPlan(taskId, {
  generationId, category, itemIds, materialIds, subjectIds,
  materialStatus = 'ready', parentReviewRunId,
} = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/supplement-plan`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      generation_id: generationId,
      category,
      item_ids: itemIds,
      material_ids: materialIds,
      subject_ids: subjectIds,
      material_status: materialStatus,
      parent_review_run_id: parentReviewRunId,
    }),
  })
  return _json(r, 'bs supplement plan')
}

export async function getBsSupplementPlan(taskId, { signal } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/supplement-plan`, {
    credentials: 'include', signal,
  })
  return _json(r, 'bs supplement plan read')
}

export async function startBsSupplementEdit(taskId) {
  const r = await _fetchMaterialMutation(`${BASE}/tasks/${taskId}/supplement-edit`, {
    method: 'POST', credentials: 'include',
  }, '�����޼�')
  return _json(r, 'bs supplement edit')
}

export async function activateBsSupplementPlan(taskId) {
  const r = await _fetchMaterialMutation(`${BASE}/tasks/${taskId}/supplement-rerun`, {
    method: 'POST', credentials: 'include',
  }, '�����˲�')
  return _json(r, 'bs supplement rerun')
}

export async function enterBsMaterialEdit(taskId) {
  const r = await _fetchMaterialMutation(`${BASE}/tasks/${taskId}/material-edit`, {
    method: 'POST', credentials: 'include',
  }, '材料修改')
  return _json(r, 'bs material edit')
}

// 报告读取（刀C 设计 100 §5）：
// - stage=draft / confirmed：均允许已启动任务读取中间结果；覆盖缺口由报告正文披露。
// - 只有尚未开始核查或输入指纹失效等技术前提不足时返回 409 结构化 detail。
// 错误对象保留 err.detail 与 err.status，供调用方区分覆盖提示与故障。
export async function getBsReport(taskId, { signal, stage = 'draft' } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/report?stage=${stage}`, { credentials: 'include', signal })
  return _json(r, 'bs report')
}

// 正式意见书读取：与报告使用同一审查代和稿别，只读取后端聚合事实。
export async function getBsOpinion(taskId, { signal, stage = 'draft' } = {}) {
  const r = await fetch(`${BASE}/tasks/${taskId}/opinion?stage=${stage}`, { credentials: 'include', signal })
  return _json(r, 'bs opinion')
}

// 人工处置回执：decision=accepted/ignored（不符合或无法核实项），note 选填；
// 旧 confirm/reject 由后端兼容；再处置覆盖（最后回执为准）。verified 不再提交。
export async function reviewBsFinding(findingId, decision, note) {
  const body = { decision }
  if (note) body.note = note
  const r = await fetch(`${BASE}/findings/${findingId}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'include',
  })
  return _json(r, 'bs review')
}

// 证据跳页：浏览器原生 PDF 视图支持 #page=N 定位
export function bsMaterialFileUrl(materialId, page) {
  return `${BASE}/materials/${materialId}/file${page ? `#page=${page}` : ''}`
}

export async function getBsMaterialPageText(materialId, page, { signal } = {}) {
  const r = await fetch(`${BASE}/materials/${materialId}/pages/${page}`, { credentials: 'include', signal })
  return _json(r, 'bs material page')
}

// SSE 事件流：afterSeq>0 显式 after_seq 续传，0 全量回放
// （浏览器断线自动重连走 Last-Event-ID 头，无需前端拼参）
export function bsEventsUrl(taskId, afterSeq) {
  return `${BASE}/tasks/${taskId}/events${afterSeq > 0 ? `?after_seq=${afterSeq}` : ''}`
}

// 后端 EVENT_LABELS 的镜像：EventSource 按事件名分发，新增后端事件类型需同步此表。
//
// ⚠️ T10-C Task C1 修复的真实缺陷：本表长期停留在最初的 11 个事件，而后端
// EVENT_LABELS 已随三批需求扩到 26 个（T1-v2 主体中心 7 个 + T4.1-T2 ZIP 派生 2 个
// + T10-B ZIP 准备生命周期 6 个）。事件名未登记时 `addEventListener` 不会触发，
// 该事件被 SSE **静默丢弃** —— 界面时间线少一段且不报错，属「护栏没人读」。
// 现按后端 EVENT_LABELS 全量补录（顺序也按后端字典顺序，便于逐行对照）。
export const BS_EVENT_TYPES = [
  'material.page_text_refresh',
  'task.supplement_planned',
  'task.supplement_blocked',
  'task.supplement_edit_started',
  'task.supplement_activated',
  'task.supplement_completed',
  'material.subject_conflict',
  'material.subject_unresolved',
  'material.page_progress',
  'material.parse_started',
  'material.supplement_linked',
  'knowledge.review_started', 'review.scope_snapshot_recorded',
  'knowledge.reference_started', 'knowledge.reference_completed',
  'knowledge.reference_discarded', 'knowledge.reference_failed', 'knowledge.reference_invalidated',
  'check.semantic_started', 'check.semantic_progress', 'check.coverage_started', 'check.rule_coverage_recorded',
  'check.ai_advisory_completed', 'check.ai_advisory_started',
  'check.ai_background_started', 'check.ai_background_progress', 'check.ai_background_completed', 'check.ai_background_failed',
  'check.ai_advisory_failed', 'check.ai_batch_checkpoint', 'check.ai_batch_progress',
  'check.negative_candidates_started', 'check.negative_candidates_finished',
  'check.negative_candidates_failed', 'check.negative_candidate_checkpoint',
  // 原始 11 个（P0-1）
  'task.created', 'material.added', 'material.replacement_requested', 'material.role_corrected', 'material.parsed',
  'evidence.anchor_recovered',
  'material.parse_failed',
  // 刀B（BS-P2-01，设计 98 §6.1）：显式替换后旧材料被标记为已替代。
  'material.superseded',
  'task.started', 'check.completed', 'check.failed',
  'task.completed', 'task.rerun', 'task.material_edit_started', 'report.exported',
  // T1-v2 主体中心（附录 §2.5）
  'subject.created', 'subject.name_confirmed', 'subject.type_confirmed', 'subject.type_locked',
  'subject.ai_type_detected', 'subject.type_conflict_detected',
  'packet.created', 'material.subject_assigned',
  // T4.1-T2 ZIP 容器派生（契约 §7.5.7）
  'zip.container_expanded', 'zip.container_failed',
  // T10-B ZIP 准备生命周期（契约 §7.3）
  'zip.preparation_started', 'zip.container_ready', 'zip.preparation_completed',
  'zip.preparation_awaiting_confirmation', 'zip.preparation_failed',
  'zip.preparation_recovered',
]

// 导出请求的错误提取与 _json 复用一个 helper（T10-A）：导出失败同样是结构化 detail，
// 两条路径必须给出完全一致的可读文案（不许一条 `[object Object]`、一条平实兜底）。
async function _blob(r, tag) {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw _httpError(body, tag, r.status)
  }
  return r.blob()
}

// 报告导出（W3-3）：Word/PDF，后端只渲染聚合结果不重算。
// stage 与 getBsReport 同口径（draft=AI 初稿 / confirmed=确认稿）；未人工处置不阻止导出，
// 仅技术前提不足时返回 409 + 结构化 detail（不由前端自行判定能否导出）。
export async function downloadBsReport(taskId, fmt, stage = 'draft', { versionNo } = {}) {
  const versionQuery = versionNo != null ? `&version_no=${encodeURIComponent(versionNo)}` : ''
  const r = await fetch(`${BASE}/tasks/${taskId}/report/export?fmt=${fmt}&stage=${stage}${versionQuery}`, { credentials: 'include' })
  return _blob(r, `bs export ${fmt}`)
}

export async function downloadBsOpinion(taskId, fmt, stage = 'draft', { versionNo, expectedDigest } = {}) {
  const versionQuery = versionNo != null ? `&version_no=${encodeURIComponent(versionNo)}` : ''
  const digestQuery = expectedDigest ? `&expected_digest=${encodeURIComponent(expectedDigest)}` : ''
  const r = await fetch(`${BASE}/tasks/${taskId}/opinion/export?fmt=${fmt}&stage=${stage}${versionQuery}${digestQuery}`, { credentials: 'include' })
  return _blob(r, `bs opinion export ${fmt}`)
}

export async function previewBsOpinion(taskId, stage = 'draft') {
  const r = await fetch(`${BASE}/tasks/${taskId}/opinion/preview-markdown?stage=${encodeURIComponent(stage)}`, { credentials: 'include' })
  const result = await _json(r, '意见书预览')
  return { markdown: result.markdown, digest: result.opinion_digest }
}

export async function downloadBsReferenceFile(taskId, referenceId, { versionNo } = {}) {
  const versionQuery = versionNo != null ? `?version_no=${encodeURIComponent(versionNo)}` : ''
  const r = await fetch(`${BASE}/tasks/${taskId}/references/${encodeURIComponent(referenceId)}/file${versionQuery}`, { credentials: 'include' })
  return _blob(r, 'bs reference file')
}

// 简化存证：报告 JSON（哈希+时间戳存证块）+ 材料原件副本 zip
export async function downloadBsEvidenceBundle(taskId, stage = 'draft') {
  const r = await fetch(`${BASE}/tasks/${taskId}/report/evidence-bundle?stage=${stage}`, { credentials: 'include' })
  return _blob(r, 'bs evidence bundle')
}

export function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
