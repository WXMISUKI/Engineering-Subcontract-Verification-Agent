import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import PlusUIShell from '../PlusUIShell'
import { usePlusContentReport } from '../usePlusContentReport'
import ReviewWorkbenchLayout from './components/ReviewWorkbenchLayout'
import useReviewWorkbench from './hooks/useReviewWorkbench'
import ReportView from './components/ReportView'
import ProjectBasicInformation from './components/ProjectBasicInformation'
import ReferenceSnapshot from './components/ReferenceSnapshot'
import MaterialPreviewModal from './components/MaterialPreviewModal'
import AiAdvisorySummary from './components/AiAdvisorySummary'
import ReviewVersionDiff from './components/ReviewVersionDiff'
import PreviousSupplementReview from './components/PreviousSupplementReview'
import ReviewPrototypeLeftRail from './components/ReviewPrototypeLeftRail'
import ReviewStepDetailPanel from './components/ReviewStepDetailPanel'
import StepOverviewPanel from './components/StepOverviewPanel'
import CheckRunView from './components/CheckRunView'
import PrototypeUploadPage from './prototype/PrototypeUploadPage'
import PrototypeMaterialsWorkspace from './prototype/PrototypeMaterialsWorkspace'
import PrototypeOverviewWorkspace from './prototype/PrototypeOverviewWorkspace'
import PrototypeFlowHeader from './prototype/PrototypeFlowHeader'
import PrototypeV3Shell from './prototype/PrototypeV3Shell'
import PrototypeSupplementOverlay from './prototype/PrototypeSupplementOverlay'
import BlockchainSupervisionModuleContainer from './prototype/BlockchainSupervisionModuleContainer'
import BlockchainSupervisionWorkspaceFrame from './prototype/BlockchainSupervisionWorkspaceFrame'
import ModelSelect from '../../components/ModelSelect'
import { buildPrototypeTask, stageFromTaskState, stageIndexOf } from './prototype/prototypeViewModel'
import './components/CheckProgress.css'
import './prototype/v3VisualTokens.css'
import {
  BS_EVENT_TYPES, bsEventsUrl, completeBsTask,
  createBsTask,
  deleteBsMaterial, downloadBsReport, downloadBsOpinion, previewBsOpinion,
  activateBsSupplementPlan, confirmBsMaterial, confirmBsEmployer, createBsSupplementPlan, enterBsMaterialEdit, getBsReport, getBsOpinion, getBsTask, getBsTodo, getBsReviewVersion, getBsRuleset,
  listBsFindings, listBsTasks, pingBs, reviewBsFinding, rerunBsTask, saveBlob, downloadBsReferenceFile,
  assignBsMaterialSubject, patchBsSection, runBsCheck, setBsMaterialRole, startBsReview, startBsSupplementEdit, uploadBsMaterial,
  startBsBackgroundAiReview, getBsBackgroundAiReview,
} from './lib/api'
import { pendingAutoCheckSteps, planAutoChecks, readAutoRunStatus, writeAutoRunStatus, clearAutoRunStatus } from './lib/autoCheckResume'
import { aiReviewRequiresNewRun, runBackgroundAiReview } from './lib/autoAiReview'
import {
  collectContainerIds,
  subjectCodeLabel, subjectNeedsNameConfirm, subjectNeedsTypeConfirm,
} from './lib/materialState'
import { buildFiveSteps, filterFindings } from './lib/fiveStepModel'
import { buildStepOverview } from './lib/stepOverviewModel'
import { buildTaskHeader } from './lib/taskHeaderModel'
import { buildMissingPlan, supplementUploadCategoryOf, matchingSupplementGeneration, supplementItemsForSubject } from './lib/todoPlan'
import { bsEtaLabel, updateBsEta } from './lib/bsEtaPlan'
import { formatEventTime } from './lib/eventTime'
import { buildMaterialsById, buildSubjectsById, groupFindings } from './lib/reviewWorkbenchModel'
import { statusLabelOf } from './lib/statusDisplay'
import { isUnreviewedFinding, reviewBucket } from './lib/reviewDecision'
import { blocksReportReview } from './lib/reviewQueueModel'

// 口径来源（不改写在这里）：业主 2026-10-14 部署文件 二、（一）五步审查 + FRS v2 §2（材料角色与启动闸）。
// 总包合同**可选（0 或 1 份）**：五步审查不以它为资料前提；未上传时由报告「范围说明」声明哪两类情形未纳入，
// 不得在启动前把缺总包渲染成硬缺口（后端同口径见 classify.validate_materials）。
// 视觉对齐 safety-review 暖棕核对表风格：#8B7355 主色 / #F7F1E9 暖底 / hairline 分组 / 全宽利用。
// 交互口径：一个会话对应一次核查——不提供「新建核查任务」入口，新核查 = 从智能体广场新建会话。
const ROLE_DEFS = [
  { key: 'general', label: '施工总承包合同', hint: '可选 · 最多 1 份' },
  { key: 'sub', label: '专业分包合同', hint: '分包合同 · 与劳务分包二选一即可' },
  { key: 'labor', label: '劳务分包合同', hint: '分包合同 · 与专业分包二选一即可' },
  { key: 'packet', label: '报审资料包', hint: '选填 · 也可包含在合同中' },
  { key: 'unknown', label: '待归类', hint: '需指定' },
]

// 用户不需要理解内部规则编号；执行顺序只在编排器内部保留。
// 内部 key（后端核查入口参数）→ 业主五步：completeness/signature = ①；a2 = ②③④；a1 = ②。
// 合同语义（semantic）与占比类（amount_ratio / workload_ratio）按业主部署文件 二、（二）（六）
// 属后续功能，本期**不列入自动核查链**：既不把预留能力算成「本期核查项」，
// 也不让它们在收口闸里要求「必须有结论」（否则任务永远收不了口）。
const AUTO_CHECK_ORDER = ['completeness', 'signature', 'a2', 'a1', 'internal_records']
// 无依据快照的旧任务以实际结论种类兼容续跑；新任务以冻结运行回执为准。
// 只有当某 kind **实际会产出结论**时才可列入，
// 否则 `every(...)` 恒假、该步骤永不跳过。
// `a1_dependency` 不得列入：R05 接线后 V16-01/L17-02 已由 illegal_subcontract 取代，
// a1_check.supersede_dependency_findings 会把它全部删掉 ⇒ 实测 5 类材料组合 + 四份金样
// 均为 0 条（2026-10-22）。它只保留在报告分区（reporting._SECTIONS）里供历史任务归类展示。
const AUTO_CHECK_KINDS = {
  completeness: ['completeness'],
  signature: ['signature_chain'],
  a2: ['cert_validity', 'person_consistency', 'qualification_match'],
  a1: ['negative_list', 'transfer_scope', 'illegal_subcontract'],
  internal_records: ['internal_records'],
}
const AUTO_CHECK_SCOPE = {
  completeness: 'A3',
  signature: 'A3',
  a2: 'A3',
  a1: 'A1',
  internal_records: 'A1',
}
const AUTO_CHECK_GROUP = {
  completeness: 'completeness',
  signature: 'signature_chain',
  a2: 'a2',
  a1: 'a1',
  internal_records: 'internal_records',
}
const AUTO_CHECK_GROUPS = new Set(Object.values(AUTO_CHECK_GROUP))

// 本地编排状态的读写（绑定当前代）统一在 lib/autoCheckResume.js 定义一次，
// 组件内不再各自持有副本。

// T10-C C2：文件选择支持 PDF 与 ZIP 报审资料包。`accept` 只是浏览器端**类型提示**
// （滤掉明显不对的扩展名），安全真相始终以后端为准（ZIP 结构双验在服务端做）。
const ACCEPT = '.pdf,application/pdf,.zip,application/zip'

// 时间线时间戳：ISO → 「09-22 15:20」（分钟精度足够，秒位是噪音）
function todayInChina() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  const part = (type) => parts.find((item) => item.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
}

function timelineTime(iso) {
  return formatEventTime(iso, { date: true })
}

function currentActivity(events, state, autoPhase, index = 0, total = 0, stage = null) {
  if (autoPhase === 'resetting') return '正在重新准备核查，请稍候'
  if (autoPhase === 'failed') return '核查中断，请重试'
  if (autoPhase === 'running') {
    if (stage === 'ai') return total > 0 ? `正在进行 AI 核查（第 ${Math.min(index + 1, total)} / ${total} 组）` : '正在进行 AI 核查'
    const last = events?.at(-1)
    if (last?.event_type?.startsWith('check.semantic_') && last.message) return last.message
    return total > 0 ? `正在执行第 ${Math.min(index + 1, total)} / ${total} 个规则核查组` : '正在执行规则核查'
  }
  if (state === 'done') return '核查完成，报告已生成'
  // ★ `review`（待人工复核）目前是**只在读守卫里出现的预留态**：后端 service.py 只有
  //   setup/preparing/running/done 四个写入点，全仓 0 处写 `review`。所以这里必须归到
  //   autoPhase 判断上——`state === 'review'` 只作前向兼容（将来补写入点时自动生效），
  //   不得再写一条独立的「等待人工复核问题」分支：它排在上一行之后是**不可达代码**，
  //   留着会让人误以为该状态真的会出现（设计 80 B-2 已记录该表述错）。
  if (autoPhase === 'done' || state === 'review') return '核查完成，请逐项复核问题'
  const last = Array.isArray(events) ? events[events.length - 1] : null
  if (last?.message) return last.message
  if (last?.label) return last.label
  if (autoPhase === 'running') return '正在自动核查材料'
  if (state === 'running') return '正在核查材料'
  return '等待上传材料'
}

// 主路径只暴露用户结果桶；事件流中的历史文案可能仍携带内部三态计数，
// 仅在进度条旁做中性投影，不改写事件、报告或详情事实。
function userFacingActivity(activity, findings) {
  const text = typeof activity === 'string' ? activity : ''
  if (!/不符合|无法核实|待处置/.test(text)) return text
  const pendingFindings = (findings || []).filter((finding) => ['hit', 'undecidable'].includes(finding?.status))
  const pending = pendingFindings.filter((finding) => finding?.open_term !== true).length
  const openTerms = pendingFindings.filter((finding) => finding?.open_term === true).length
  const denominatorNote = openTerms > 0
    ? `；另有 ${openTerms} 项仅登记条款，不计入可构成结论统计`
    : ''
  return pending > 0
    ? `已识别 ${pending} 项可构成结论的待处理问题${denominatorNote}`
    : openTerms > 0
      ? `当前无可构成结论的待处理问题${denominatorNote}`
      : '核查进展已更新，请查看待处理项'
}

// 同一份报告可能同时投影原合同与补件材料的相同读取提示。
// 报告层没有稳定 material_id 时，只去掉完全相同的 file_name+message，
// 不按文件名单独合并，避免把不同材料的不同降级事实误删。
export function uniqueMaterialIssues(issues) {
  const seen = new Set()
  return (issues || []).filter((issue) => {
    const key = `${issue?.file_name || ''}\u0000${issue?.message || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const REVIEW_VERSION_TRIGGER_LABELS = {
  completed: '收口时保存',
  rerun: '重新核查前保存',
  material_edit: '补充材料前保存',
}

// A-15（B1）合同要素（extract 层产出）呈现：字段顺序与展示名镜像后端 extract.FIELD_LABELS。
// 与报告 contract_facts[].fields 的键一一对应；数值单位来自字段自身的 unit。
const CONTRACT_FACT_ORDER = [
  'contract_type', 'amount', 'scope', 'parties', 'sign_date',
  'mgmt_fee', 'material_fee', 'plan_author', 'declarations',
]
const CONTRACT_FACT_LABELS = {
  contract_type: '合同类型',
  amount: '合同金额',
  scope: '工程范围/内容',
  parties: '甲乙方名称',
  sign_date: '签订日期',
  mgmt_fee: '管理费约定',
  material_fee: '材料款承担方',
  plan_author: '施工方案编制主体',
  declarations: '声明性条款',
}

// 字段值 → 展示文本：数值带 unit；主体为对象 → k：v 拼接；列表 → 顿号拼接。
function contractFactValueText(f) {
  const v = f.value
  let text
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    text = Object.entries(v).map(([k, val]) => `${k}：${val}`).join('；')
  } else if (Array.isArray(v)) {
    text = v.join('、')
  } else if (typeof v === 'number' && Number.isInteger(v)) {
    text = String(v)
  } else {
    text = v === null || v === undefined ? '' : String(v)
  }
  return `${text}${f.unit || ''}`
}

export default function BlockchainSupervisionWorkbench({ conversationId, onExitPlus, sidebarOpen, onToggleSidebar, onContentChange, initialTaskId = null }) {
  // 深链 ?task_id=...(由宿主解析后传入):恢复时优先打开指定任务。
  const deepLinkTaskId = Number(initialTaskId) || null
  const [phase, setPhase] = useState('loading') // setup | materials | workbench | error
  const [modelCode, setModelCode] = useState('')
  const [taskId, setTaskId] = useState(null)
  const [activeTaskTitle, setActiveTaskTitle] = useState('')
  const [error, setError] = useState('')
  // T9-A：恢复失败的显式原因与重试计数（本地态，不引入全局状态管理）。
  // 原因只由 restore-error 面板承载，不再同时塞进顶栏 banner —— 避免同一文案两处 DOM
  // （R1A 已踩过：同文案两处会导致定位歧义，也让「唯一解释」变模糊）。
  const [restoreError, setRestoreError] = useState('')
  const [retryTick, setRetryTick] = useState(0)
  const [autoRunOnMount, setAutoRunOnMount] = useState(false)
  const [editingMaterials, setEditingMaterials] = useState(false)
  // 刀A：从待办「补件」进入材料阶段时带回的补件要求（材料类 label/hint/可消条数/缺件标签）。
  // 只用于 setup 阶段提示与上传类别预选，不参与任何核查判定。
  const [pendingSupplement, setPendingSupplement] = useState(null)
  const [pendingEditSupplement, setPendingEditSupplement] = useState(null)
  const [supplementOverlay, setSupplementOverlay] = useState(null)
  const [queuedSupplementFile, setQueuedSupplementFile] = useState(null)
  // 「重新开始」：已开始的任务必须退回到材料阶段，后端只有 material-edit 这一条路，
  // 它会作废旧结论（service.enter_material_edit），所以先弹确认，不能一点就作废。
  const [restartConfirm, setRestartConfirm] = useState(false)
  const [reviewNotesOpen, setReviewNotesOpen] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const restartDialogRef = useRef(null)
  const restartCancelRef = useRef(null)
  const restartReturnFocusRef = useRef(null)
  // 同一个确认弹层承担两种破坏性入口：重新开始 / 修改材料（都走 material-edit 作废旧结论）。
  const [confirmIntent, setConfirmIntent] = useState('restart')
  // 上传阶段没有结论可作废：重新开始只重置本页的本地选择（类别/主体/标段草稿），
  // 已上传材料在后端任务里原样保留。
  const [setupNonce, setSetupNonce] = useState(0)
  // 四阶段流程条的「已到达」上界（原型 state.visited 的单调版）：页面级唯一记录点，
  // 跨 setup/materials/workbench 三个树共用；结论被作废（material-edit）时复位到 0。
  // 只用来判「可点」，当前高亮仍由各树自己的所在阶段决定。
  const [reachedIndex, setReachedIndex] = useState(0)
  const reachStage = useCallback((index) => {
    if (!Number.isInteger(index)) return
    setReachedIndex((prev) => (index > prev ? index : prev))
  }, [])
  // 从材料查看态返回工作台时落回用户离开的那一屏（不是永远跳复核）。
  const [workbenchView, setWorkbenchView] = useState(null)

  // 会话已有内容 = 已建核查任务(setup 壳也算,taskId 由上传材料时 ensureTask 产生)。
  // 宿主侧边栏「新建对话」据此放行;空 setup(未传材料)不放行,避免堆重复空会话。
  usePlusContentReport(onContentChange, phase !== 'loading' && taskId != null)

  const openWorkbench = useCallback((id, { autoRun = false, view = null, title = '' } = {}) => {
    setTaskId(id)
    if (title) setActiveTaskTitle(title)
    setWorkbenchView(view)
    setPhase('workbench')
    setAutoRunOnMount(autoRun)
  }, [])
  // 流程条「上传材料」：只进查看态，不调 material-edit、不作废结论（2026-10-29）。
  // 要真正改材料由页面内的「修改材料」显式发起并二次确认。
  const openMaterials = useCallback((id, view = null) => {
    if (id == null) return
    setTaskId(id)
    setWorkbenchView(view)
    setPhase('materials')
  }, [])
  // 材料查看态 → 回工作台某个已到达阶段；上传阶段自身不做事。
  const goToStage = useCallback((key) => {
    if (!key || taskId == null) return
    if (key === 'upload') {
      openMaterials(taskId, workbenchView || 'review')
      return
    }
    reachStage(stageIndexOf(key))
    if (key === 'overview') {
      openWorkbench(taskId, { view: 'overview' })
      return
    }
    openWorkbench(taskId, { view: key === 'run' ? 'run' : key === 'report' ? 'report' : 'review' })
  }, [openMaterials, openWorkbench, reachStage, taskId, workbenchView])
  const openSetup = useCallback((id = null, { editing = false, supplement = null } = {}) => {
    setTaskId(id || null)
    setEditingMaterials(editing)
    setPendingSupplement(supplement)
    setPhase('setup')
  }, [])

  const handlePreparationNeedsAction = useCallback((id) => {
    setError('材料准备未完成，请检查材料和主体后重新开始核查')
    openSetup(id, { editing: true })
  }, [openSetup])

  // 刀A：待办「补件」→ 复用既有 material-edit 语义（旧版留档只读、当前结论作废、需重新核查
  // 生成新审查代）。补件要求随任务一起带回 setup，用于提示与上传类别预选。
  const beginMaterialEdit = useCallback(async (id, supplement = null) => {
    try {
      if (supplement?.supplementMode) await startBsSupplementEdit(id)
      else await enterBsMaterialEdit(id)
      setError('')
      // 结论已作废、findings 已删除：流程条进度回到上传阶段，后续阶段不再可点。
      setReachedIndex(0)
      // 作废后本代身份即失效；这里再显式清一次本地编排标记，避免任何读取路径
      // 在「新代尚未 start」的窗口里拿到上一代的 completed（双保险，主判据在代校验）。
      clearAutoRunStatus(id)
      openSetup(id, { editing: true, supplement })
    } catch (e) {
      setError(e.message)
      throw e
    }
  }, [openSetup])

  const handleRestart = useCallback(() => {
    if (phase === 'workbench' || phase === 'materials') {
      restartReturnFocusRef.current = document.activeElement
      setConfirmIntent('restart')
      setRestartConfirm(true)
      return
    }
    setSetupNonce((n) => n + 1)
  }, [phase])

  // 工作台抬头的「修改材料」与材料查看态的「修改材料」都要先说明会作废结论。
  const requestMaterialEdit = useCallback(() => {
    restartReturnFocusRef.current = document.activeElement
    setPendingEditSupplement(null)
    setConfirmIntent('edit')
    setRestartConfirm(true)
  }, [])

  const requestSupplementMaterialEdit = useCallback((supplement) => {
    restartReturnFocusRef.current = document.activeElement
    setPendingEditSupplement(supplement || null)
    setConfirmIntent('edit')
    setRestartConfirm(true)
  }, [])

  // v3 原型：任务头「上传 / 补件」先打开覆盖层，只有用户选定文件并提交后，
  // 才进入既有的 material-edit 作废闸门；点击任务头本身不产生后端写入。
  const openSupplementOverlay = useCallback((context = {}) => {
    setSupplementOverlay({
      materialLabel: context.materialLabel || '待补材料',
      stepLabel: context.stepLabel || '第 1 步 · 要素完整性核查',
      uploadCategory: context.uploadCategory || null,
      category: context.category || null,
      supplementMode: context.supplementMode === true,
      itemId: context.itemId ?? null,
      itemIds: Array.isArray(context.itemIds) ? context.itemIds : [],
      subjectItems: Array.isArray(context.subjectItems) ? context.subjectItems : [],
      subjectIds: Array.isArray(context.subjectIds) ? context.subjectIds : [],
      subjectOptions: Array.isArray(context.subjectOptions) ? context.subjectOptions : [],
      materialIds: Array.isArray(context.materialIds) ? context.materialIds : [],
      replacesMaterialId: context.replacesMaterialId ?? null,
      subjectId: context.subjectId ?? null,
      packetId: context.packetId ?? null,
    })
  }, [])

  const submitSupplementOverlay = useCallback(({ file, note, materialLabel, stepLabel, uploadCategory, replacesMaterialId, subjectId, packetId }) => {
    setSupplementOverlay(null)
    setQueuedSupplementFile({ file, note })
    setPendingEditSupplement({
      label: materialLabel,
      hint: note || `补充${materialLabel}`,
      uploadCategory: uploadCategory || 'labor',
      category: supplementOverlay?.category || null,
      itemId: subjectId != null && (supplementOverlay?.subjectItems?.length || supplementOverlay?.subjectIds?.length > 1)
        ? null : (supplementOverlay?.itemId ?? null),
      itemIds: supplementItemsForSubject(supplementOverlay, subjectId),
      subjectIds: subjectId != null ? [subjectId] : (Array.isArray(supplementOverlay?.subjectIds) ? supplementOverlay.subjectIds : []),
      materialIds: Array.isArray(supplementOverlay?.materialIds) ? supplementOverlay.materialIds : [],
      supplementMode: supplementOverlay?.supplementMode === true,
      stepLabel,
      replacesMaterialId: replacesMaterialId ?? null,
      subjectId: subjectId ?? null,
      packetId: packetId ?? null,
    })
    setConfirmIntent('edit')
    setRestartConfirm(true)
  }, [supplementOverlay])

  useEffect(() => {
    if (!restartConfirm) {
      restartReturnFocusRef.current?.focus?.()
      return undefined
    }
    const dialog = restartDialogRef.current
    restartCancelRef.current?.focus?.()
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setRestartConfirm(false)
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusables = Array.from(dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
        .filter((node) => !node.disabled && !node.hidden)
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
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [restartConfirm])

  const confirmRestart = useCallback(async () => {
    if (restarting || taskId == null) { setRestartConfirm(false); return }
    setRestarting(true)
    try {
      let supplementGenerationId = null
      if (pendingEditSupplement?.supplementMode) {
        supplementGenerationId = `supplement-${taskId}-${Date.now()}`
        await createBsSupplementPlan(taskId, {
          generationId: supplementGenerationId,
          category: pendingEditSupplement.category || pendingEditSupplement.uploadCategory || 'unknown',
          itemIds: pendingEditSupplement.itemIds?.length
            ? pendingEditSupplement.itemIds
            : (pendingEditSupplement.itemId ? [pendingEditSupplement.itemId] : undefined),
          materialIds: pendingEditSupplement.materialIds?.length
            ? pendingEditSupplement.materialIds
            : (pendingEditSupplement.replacesMaterialId ? [pendingEditSupplement.replacesMaterialId] : undefined),
          subjectIds: pendingEditSupplement.subjectIds?.length
            ? pendingEditSupplement.subjectIds
            : (pendingEditSupplement.subjectId ? [pendingEditSupplement.subjectId] : undefined),
        })
      }
      await beginMaterialEdit(taskId, pendingEditSupplement && {
        ...pendingEditSupplement, generationId: supplementGenerationId,
      })
      if (queuedSupplementFile?.file) {
        try {
          await uploadBsMaterial(taskId, queuedSupplementFile.file, {
            role: pendingEditSupplement?.uploadCategory || 'labor',
            materialCategory: pendingEditSupplement?.uploadCategory || 'labor',
            // 替换目标的主体/类别由后端从旧材料原子继承，避免前端重复猜测归属。
            replacesMaterialId: pendingEditSupplement?.replacesMaterialId ?? undefined,
            subjectId: pendingEditSupplement?.subjectId ?? undefined,
            packetId: pendingEditSupplement?.packetId ?? undefined,
            supplementGenerationId,
          })
        } catch (e) {
          setError(`${queuedSupplementFile.file.name}：${e.message}`)
        }
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setRestarting(false)
      setRestartConfirm(false)
      setPendingEditSupplement(null)
      setQueuedSupplementFile(null)
    }
  }, [restarting, taskId, beginMaterialEdit, pendingEditSupplement, queuedSupplementFile])

  // 首载：恢复本会话绑定的任务（一会话一核查）。已开始→工作台；仅建壳未开始→回到 setup 并带回已传材料。
  // T9-A：仍严格按传入的 conversationId 查找，不使用「最近任务/全局最新任务」猜测会话（§三.3）。
  useEffect(() => {
    if (!conversationId) { openSetup(); return }
    let dead = false
    listBsTasks(conversationId).then(({ tasks }) => {
      if (dead) return
      // 深链显式指定的任务优先:运行/已审查→工作台;setup/preparing→材料阶段。
      if (deepLinkTaskId != null) {
        const desired = tasks.find((t) => Number(t.task_id) === deepLinkTaskId)
        if (desired) {
            if (['preparing', 'running', 'review', 'done'].includes(desired.state)) {
              openWorkbench(desired.task_id, { title: desired.title, autoRun: true, view: desired.state === 'preparing' ? 'run' : undefined })
          } else {
            openSetup(desired.task_id)
          }
          return
        }
        // 深链指定的任务不存在:像 OC/SC 一样明确提示,停在材料/空态,
        // 不静默打开同会话里的另一个任务。
        setRestoreError('目标任务不存在或已删除')
        setPhase('error')
        return
      }
        const started = tasks.find((t) => t.state === 'preparing' || t.state === 'running' || t.state === 'review' || t.state === 'done')
        if (started) { openWorkbench(started.task_id, { title: started.title, autoRun: started.state === 'preparing', view: started.state === 'preparing' ? 'run' : undefined }); return }
      // T10-C C3：`setup` 与 `preparing` 都进第一阶段。`preparing` 是 ZIP 资料包准备中的
      // 持久任务态（契约 §7.3），此时**结论尚未产生** —— 若按「任务已开始」跳进工作台，
      // 用户看到的是一页空结论；故交给 SetupStage 呈现「资料准备中」并轮询到收口，
      // 由收口结果决定进 workbench 还是回 setup 显示原因。
      const inSetupStage = tasks.find((t) => t.state === 'setup' || t.state === 'preparing')
      openSetup(inSetupStage ? inSetupStage.task_id : null)
    }).catch((e) => {
      if (dead) return
      // T9-A §三.4：恢复失败不得静默降级为 setup —— 那会把「会话/任务读取失败」伪装成
      // 「本会话没有任务」，用户会据此重新上传，产生重复任务。改为明确错误态 + 重试。
      setRestoreError(e.message)
      setPhase('error')
    })
    return () => { dead = true }
  }, [conversationId, retryTick, openWorkbench, openSetup, deepLinkTaskId])

  // A-10 健康探针（B0）：进入工作台先探后端——服务未就绪/网络不通时给明确提示，
  // 而不是等用户点上传才报笼统错。ping 无需鉴权，只在真连不上时亮横幅。
  useEffect(() => {
    let dead = false
    pingBs()
      .then((r) => { if (!dead && r && r.ok === false) setError('智能体服务未就绪，请稍后重试') })
      .catch((e) => { if (!dead) setError(`无法连接智能体服务：${e.message}`) })
    return () => { dead = true }
  }, [])

  return (
    <PlusUIShell
      title="工程分包审查智能体"
      subtitle="工程分包合规"
      billing={{ scopeType: 'bs_task', scopeId: taskId }}
      showModelSelect={false}
      headerModelSelector={<ModelSelect value={modelCode} onChange={setModelCode} placement="bottom" />}
      dataTestId="plus-ui-shell"
      moduleCode="blockchain-supervision"
      ariaLabel="通用 Plus 工作台容器：工程分包审查智能体"
      sidebarOpen={sidebarOpen}
      onToggleSidebar={onToggleSidebar}
      actions={onExitPlus ? (
        <button
          onClick={onExitPlus}
          className="inline-flex items-center gap-1.5 rounded-[6px] border border-[#E5E7EB] px-2.5 py-1 text-xs text-[#6B7280] hover:bg-[#F3F4F6]"
          title="退出 Plus，回到普通对话"
        >
          <RotateCcw className="h-3.5 w-3.5" /> 退出 Plus
        </button>
      ) : null}
    >
      <BlockchainSupervisionWorkspaceFrame phase={phase}>
      <BlockchainSupervisionModuleContainer phase={phase}>
      <div className="flex min-h-0 flex-1 flex-col">
        {error && (
          <div className="mx-8 mt-3 rounded-[6px] border border-[#F5C6C6] bg-[#FDECEA] px-3 py-2 text-[13px] text-[#C62828]">
            {error}
            <button className="ml-2 underline" onClick={() => setError('')}>关闭</button>
          </div>
        )}
        {phase === 'loading' && (
          <div className="flex flex-1 items-center justify-center text-sm text-tertiary">加载中…</div>
        )}
        {phase === 'error' && (
          // T9-A §三.4：无效/读取失败的会话给明确空态（说明缺什么、下一步怎么做），
          // 而不是静默串入其他会话或伪装成「本会话无任务」的 setup。
          <div data-testid="restore-error" className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-sm text-[#C62828]">无法恢复本会话的核查任务</p>
            {restoreError && <p data-testid="restore-error-reason" className="text-sm text-[#C62828]">{restoreError}</p>}
            <p className="max-w-md text-[13px] leading-relaxed text-[#6B7280]">
              会话标识：{conversationId ?? '（缺失）'}。可能是会话已失效，或服务暂时不可用。
              请重试；若持续失败，请退出 Plus 后从智能体广场重新进入该会话。
            </p>
            <button
              type="button"
              data-testid="restore-retry"
              onClick={() => { setRestoreError(''); setPhase('loading'); setRetryTick((n) => n + 1) }}
              className="rounded-[6px] border border-[#E5E7EB] px-3 py-1.5 text-xs text-[#6B7280] hover:bg-[#F3F4F6]"
            >
              重试
            </button>
          </div>
        )}
        {phase === 'setup' && (
          <SetupStage key={`setup-${setupNonce}`} conversationId={conversationId} taskId={taskId} editing={editingMaterials}
          supplement={pendingSupplement} reachedIndex={reachedIndex}
            onReset={handleRestart} resetting={restarting}
            onTaskCreated={setTaskId} onStarted={(id) => { setError(''); openWorkbench(id, { autoRun: true, view: 'run' }) }} onError={setError} />
        )}
        {phase === 'materials' && (
          <SetupStage key={`materials-${taskId}`} conversationId={conversationId} taskId={taskId} readOnly
          reachedIndex={reachedIndex} onStageSelect={goToStage} onRequestEdit={requestMaterialEdit}
          onExit={() => openWorkbench(taskId, { view: workbenchView || 'review' })}
            onReset={handleRestart} resetting={restarting}
          onTaskCreated={setTaskId} onStarted={(id) => { setError(''); openWorkbench(id, { autoRun: true, view: 'run' }) }} onError={setError} />
        )}
        {phase === 'workbench' && (
          <WorkbenchStage key={`${conversationId}:${taskId}`} taskId={taskId} autoRunOnMount={autoRunOnMount} onError={setError}
          onPreparationNeedsAction={handlePreparationNeedsAction}
          initialView={workbenchView} reachedIndex={reachedIndex} onReachStage={reachStage}
            onStageSelect={goToStage} onOpenMaterials={openMaterials} onEditMaterials={beginMaterialEdit} onReset={handleRestart} resetting={restarting}
            onOpenReviewNotes={() => setReviewNotesOpen(true)} onRequestSupplementMaterials={requestSupplementMaterialEdit} onRequestEditMaterials={requestMaterialEdit} onOpenSupplementOverlay={openSupplementOverlay} />
        )}
      </div>
      {reviewNotesOpen && phase === 'workbench' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6" data-testid="prototype-review-notes-overlay">
          <div role="dialog" aria-modal="true" aria-labelledby="prototype-review-notes-title" className="w-[min(680px,100%)] rounded-[10px] border border-[#DCE5EE] bg-white p-5 shadow-xl">
            <div className="flex items-center gap-3">
              <h2 id="prototype-review-notes-title" className="text-[17px] font-bold text-[#17283D]">评审说明</h2>
              <button type="button" className="ml-auto rounded border border-[#DCE5EE] px-2.5 py-1 text-[12px] text-[#587089]" onClick={() => setReviewNotesOpen(false)}>关闭</button>
            </div>
            <div className="mt-4 space-y-2 text-[13px] leading-relaxed text-[#587089]">
              <p>第 1 步集中处理材料完整性；第 2—5 步只展示规则异常和证据。</p>
              <p>人工处置只记录采纳、忽略和说明，不覆盖系统原始事实；未人工处理项会在报告中明确标注。</p>
              <p>条款全文、页码和历史版本在问题详情或全屏对照中查看。</p>
            </div>
          </div>
        </div>
      )}
      {supplementOverlay && phase === 'workbench' && (
        <PrototypeSupplementOverlay
          materialLabel={supplementOverlay.materialLabel}
          stepLabel={supplementOverlay.stepLabel}
          uploadCategory={supplementOverlay.uploadCategory}
          replacesMaterialId={supplementOverlay.replacesMaterialId}
          subjectId={supplementOverlay.subjectId}
          subjectOptions={supplementOverlay.subjectOptions}
          packetId={supplementOverlay.packetId}
          onCancel={() => setSupplementOverlay(null)}
          onSubmit={submitSupplementOverlay}
        />
      )}
      {restartConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6" data-testid="prototype-restart-confirm">
          <div ref={restartDialogRef} role="dialog" aria-modal="true" aria-labelledby="prototype-restart-confirm-title" aria-describedby="prototype-restart-confirm-description" className="w-[min(520px,100%)] rounded-[10px] border border-[#E9E3D9] bg-white p-5 shadow-xl">
            <h2 id="prototype-restart-confirm-title" className="text-[17px] font-bold">{confirmIntent === 'edit' ? '修改材料？' : '重新开始核查？'}</h2>
            <p id="prototype-restart-confirm-description" className="mt-2 text-[14px] leading-relaxed text-[#6B7280]">
              本次核查结论将作废并留档为历史版本；已上传材料和标段信息保留，需要重新核查才会生成新结论。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button ref={restartCancelRef} type="button" className="rounded-lg border border-[#E9E3D9] px-3 py-2 text-[14px]" onClick={() => setRestartConfirm(false)}>取消</button>
              <button type="button" data-testid="prototype-restart-confirm-ok" disabled={restarting} className="rounded-lg bg-[#8B7355] px-4 py-2 text-[14px] font-bold text-white disabled:opacity-60" onClick={confirmRestart}>{restarting ? '处理中…' : (confirmIntent === 'edit' ? '修改材料' : '重新开始')}</button>
            </div>
          </div>
        </div>
      )}
      </BlockchainSupervisionModuleContainer>
      </BlockchainSupervisionWorkspaceFrame>
    </PlusUIShell>
  )
}

/* ==================== 两段式 · 第一段：上传材料并开始核查（07 §4.1） ==================== */

  function SetupStage({ conversationId, taskId, editing = false, readOnly = false, supplement = null,
    reachedIndex = 0, onStageSelect, onRequestEdit, onExit, onReset, resetting = false, onTaskCreated, onStarted, onError }) {
  const [detail, setDetail] = useState(null)           // getBsTask 返回
  const [detailError, setDetailError] = useState('')
  // 默认北京时间当天，用户可在启动前修改；历史记录以已保存日期为准。
  const [section, setSection] = useState(() => ({ projectName: '', contractSection: '', baselineDate: todayInChina() }))
  const baselineManualRef = useRef(false)
  const [uploadsInFlight, setUploadsInFlight] = useState(0)
  const [confirmModal, setConfirmModal] = useState(null) // validation payload
  const [submitting, setSubmitting] = useState(false)
  // T10-C C3：`preparing` 是**服务端持久态**，本地态只是它的即时镜像（刷新后由
  // get_task 的 state 重建，故不落会话存储——真值只有一个来源：后端任务态）。
  const [preparing, setPreparing] = useState(false)
  const [etaNow, setEtaNow] = useState(() => Date.now())
  const etaRef = useRef(null)
  // T10-C C3 滞回：是否**已观察到**准备/运行态。受理 preparing 后的第一次详情读取
  // 可能仍是受理前的 `setup` 快照（请求与提交存在天然时序差）；若不设滞回，
  // 轮询会把「陈旧快照」误判成「收口回退」，准备态横幅一闪即灭。
  const sawZipProgressRef = useRef(false)
  const inputRef = useRef(null)
  // 上传阶段只记录用户的归属选择；完整性和规则结论留到正式核查。
  // 刀A：从待办「补件」进入时按补件要求预选类别。
  // 预选值由 lib/todoPlan 依后端 roles 唯一算好（面板 item.uploadCategory），这里不重算。
  const [uploadCategory, setUploadCategory] = useState(
    // 原型初始选择为“劳务分包合同”；补件场景仍以任务计划预选类别为准。
    () => supplement?.uploadCategory || 'labor',
  )
  const [uploadSubject, setUploadSubject] = useState('')
  const subjectSelectionInitialized = useRef(false)
  // 补件提示可手动收起（用户可能改主意、直接开始核查）；收起不改变任何后端状态。
  const [supplementDismissed, setSupplementDismissed] = useState(false)
  const showSupplement = !!(supplement && supplement.label) && !supplementDismissed

  const materials = detail?.materials || []
  const subjectOptions = useMemo(() => {
    // 显式创建的主体允许先建主体再上传材料；材料自动主体则必须仍有当前材料引用。
    // 后端已过滤 retired 主体，这里再按材料角色收窄，避免旧快照把孤立主体带回下拉框。
    const linkedIds = new Set(materials
      .filter((m) => ['sub', 'labor', 'packet'].includes(m.role) && m.subject_id != null)
      .map((m) => String(m.subject_id)))
    return (detail?.subjects || [])
      .filter((s) => s.origin !== 'material_auto' || linkedIds.has(String(s.subject_id)))
      .filter((s) => s.subject_type === 'professional' || s.subject_type === 'labor'
        || subjectNeedsTypeConfirm(s) || subjectNeedsNameConfirm(s))
      .map((s) => ({
      key: String(s.subject_id),
      // 编号只作身份标识，不带创建时的类型前缀（编号生成后不随类型变更改写）。
      label: s.name ? `${subjectCodeLabel(s.stable_code)} · ${s.name}` : subjectCodeLabel(s.stable_code),
      }))
  }, [detail?.subjects, materials])

  const supplementView = useMemo(() => {
    if (!supplement) return null
    const subject = subjectOptions.find((item) => item.key === uploadSubject)
    return {
      ...supplement,
      subjectName: subject?.label || '',
      subjectAutoSelected: subjectSelectionInitialized.current && uploadSubject === String(supplement.subjectId),
      subjectMappingReliable: supplement.subjectMappingReliable !== false,
    }
  }, [supplement, subjectOptions, uploadSubject])

  // 补件计划只有在主体映射明确且仍存在于当前任务时才自动带入；多主体/不可靠映射保持人工选择。
  useEffect(() => {
    if (!supplement || supplement.subjectId == null || subjectSelectionInitialized.current) return
    const key = String(supplement.subjectId)
    if (subjectOptions.some((item) => item.key === key)) {
      subjectSelectionInitialized.current = true
      setUploadSubject(key)
    }
  }, [supplement?.subjectId, subjectOptions])
  const busy = uploadsInFlight > 0 || submitting
  const validation = detail?.validation

  const parseProgress = detail?.progress?.current_task
  const isPreparing = preparing || detail?.state === 'preparing'
  useEffect(() => {
    if (!isPreparing || !parseProgress) {
      etaRef.current = null
      return
    }
    etaRef.current = updateBsEta(etaRef.current, parseProgress)
  }, [isPreparing, parseProgress?.material_id, parseProgress?.run_id, parseProgress?.source, parseProgress?.done_pages, parseProgress?.total_work_pages])
  useEffect(() => {
    if (!isPreparing) return undefined
    const timer = setInterval(() => setEtaNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [isPreparing])

  // 恢复未完成的 setup 任务
  useEffect(() => {
    if (!taskId) return
    setDetailError('')
    getBsTask(taskId).then((d) => {
      setDetail(d)
      baselineManualRef.current = d.baseline_date_source === 'manual' || (!d.baseline_date_source && !!d.section?.baseline_date)
      // A-03：恢复未完成 setup 时回填已保存的标段（历史恢复/刷新不丢）
      if (d.section) setSection({
        projectName: d.section.project_name || d.section_suggestions?.project_name?.value || '',
        contractSection: d.section.contract_section || (
          d.section.project_name && d.section_suggestions?.contract_section?.derived_from_project &&
          d.section.project_name.replace(/\s+/g, '') !== d.section_suggestions.contract_section.derived_from_project.replace(/\s+/g, '')
            ? '' : d.section_suggestions?.contract_section?.value || ''),
        baselineDate: d.state === 'setup' && !readOnly && !baselineManualRef.current ? todayInChina() : (d.section.baseline_date || ''),
      })
      // T10-C C3：只有**真正的核查态**才进工作台。`preparing`（资料包准备中）必须留在
      // 第一阶段——此时结论尚未产生，跳进工作台只会让用户看到一页空结论（§三 冻结口径）。
      // 只读查看态是「回看材料」，不是进入核查：不得把用户弹回工作台。
      if (!editing && !readOnly && (d.state === 'running' || d.state === 'review' || d.state === 'done')) onStarted(d.task_id)
    }).catch((e) => { setDetailError(e.message || '请刷新后重试') })
  }, [taskId, editing, readOnly, onStarted, onError])

  const refresh = useCallback(async (id) => {
    const d = await getBsTask(id)
    setDetail(d)
    return d
  }, [])

  // ── T10-C C3：准备态轮询（**复用任务详情轮询，不新增实时框架**）──
  // 只要后端任务态是 `preparing` 就轻量轮询详情（1.5s），直到它变成 running/review/done
  // 或退回 setup：
  //   · 转 running → 自动进入 workbench（准备完成且无缺口）；
  //   · 回 setup   → **留在第一阶段**并显示可读原因（类型缺口 / 资料包失败），不锁死。
  // 轮询在 effect 清理时取消，组件卸载或态变化即停，不留悬挂定时器。
  useEffect(() => {
    if (!taskId) return
    if (!(preparing || detail?.state === 'preparing')) return
    let dead = false
    const tick = async () => {
      try {
        const d = await getBsTask(taskId)
        if (dead) return
        setDetail(d)
        if (d.state === 'running' || d.state === 'review' || d.state === 'done') {
          sawZipProgressRef.current = true
          setPreparing(false)
          onStarted(d.task_id)
          return
        }
        if (d.state === 'preparing') {
          sawZipProgressRef.current = true   // 已确证进入准备段，后续 setup 才算真回退
          return
        }
        if (d.state === 'setup') {
          // 滞回：受理后第一次读取可能拿到受理前的 setup 快照 → 不是回退，继续等下一轮。
          if (!sawZipProgressRef.current) return
          // 真·收口回 setup：原因由第一阶段的 readiness 阻断项与类型提示承接，这里只回退展示态。
          setPreparing(false)
        }
      } catch (e) {
        if (!dead) onError(e.message)
      }
    }
    const timer = setInterval(tick, 1500)
    tick()  // 首次立即探一次，避免白等一个周期
    return () => { dead = true; clearInterval(timer) }
  }, [taskId, preparing, detail?.state, onStarted, onError])

  const ensureTask = useCallback(async () => {
    if (taskId) return taskId
    const title = `分包合规核查 ${new Date().toISOString().slice(0, 10)}`
    const created = await createBsTask({
      conversationId, title, scopeA1: true, scopeA3: true,
      // A-03（B1）：三字段随创建一并提交（空则后端存 NULL）
      projectName: section.projectName,
      contractSection: section.contractSection,
      baselineDate: baselineManualRef.current ? section.baselineDate : null,
    })
    onTaskCreated(created.task_id) // 任务与本会话 1:1 绑定；先同步父态再继续上传
    return created.task_id
  }, [conversationId, taskId, section, onTaskCreated])

  // A-03（B1）：标段字段已建任务则 PATCH /section（setup 段可改），未建任务先存本地态，创建时随体提交
  const changeSection = useCallback((key, value) => {
    if (key === 'baselineDate') {
      baselineManualRef.current = !!value
      if (!value) value = todayInChina()
    }
    setSection((prev) => ({ ...prev, [key]: value }))
    if (taskId) patchBsSection(taskId, { [key]: key === 'baselineDate' && !baselineManualRef.current ? null : value }).catch((e) => onError(e.message))
  }, [taskId, onError])

  // 上传（含替换）后等解析收口的共用节奏：前 5 轮 600ms，之后 2s 退避，
  // 总预算 180s（对齐 OCR 实测上限）；预算耗尽静默退出，不带病进核查。
  const waitForParsing = useCallback(async (id) => {
    const deadline = Date.now() + 180_000
    let round = 0
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, round < 5 ? 600 : 2000))
      round += 1
      const d = await getBsTask(id)
      setDetail(d)
      if (!(d.materials || []).some((m) => m.parse_state === 'parsing')) return
    }
  }, [])

  const addFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || []).filter((f) => f.size > 0)
    if (!files.length) return
    try {
      const id = await ensureTask()
      setUploadsInFlight(files.length)
      for (const f of files) {
        try {
          const uploaded = await uploadBsMaterial(id, f, {
            role: uploadCategory,
            subjectId: uploadCategory === 'packet' ? uploadSubject || null : null,
            materialCategory: uploadCategory,
            supplementGenerationId: showSupplement
              ? matchingSupplementGeneration(supplement, uploadCategory, uploadSubject) : null,
          })
        } catch (e) {
          onError(`${f.name}：${e.message}`)
        }
        setUploadsInFlight((n) => n - 1)
      }
      await refresh(id)
      // P2-2：解析/判章在后台线程收口——清单里还有「解析中」就轮询到结束。
      // P1-Q2：扫描件走 OCR 兜底实测 15–60s+，固定 600ms×20=12s 远不够——
      // 改自适应节奏：前 5 轮 600ms（普通 PDF 秒级收口），之后 2s 退避，
      // 总预算 180s（对齐 OCR 实测上限）。预算耗尽静默退出：后端 409 闸
      // 保证解析中不能开始核查，用户刷新清单即可，不带病进核查。
      // T10-C C2：ZIP 容器停在 `stored`（原包已存、等准备阶段展开），它**不会**自行离开
      // 该态——只等 `parsing` 才成立，否则 ZIP 上传后这里会白轮询满 180s。
      await waitForParsing(id)
    } catch (e) {
      onError(e.message)
      setUploadsInFlight(0)
    }
  }, [ensureTask, onError, refresh, uploadCategory, uploadSubject, waitForParsing, showSupplement, supplement])

  // 刀B（BS-P2-01）：材料替换。行内「替换」→ 原生选文件 → 带 replaces_material_id 上传。
  // 归类/主体沿用被替换行：替换是同一份材料的新版本，不要求用户再选一次类型。
  // 成功后刷新任务详情；旧行由后端标 superseded_by_id，界面显示「已被替换」与替代目标。
  const replaceMaterial = useCallback(async (materialId, file) => {
    if (!taskId || !file) return
    const target = materials.find((m) => (m.id ?? m.material_id) === materialId)
    setUploadsInFlight((n) => n + 1)
    try {
      await uploadBsMaterial(taskId, file, {
        role: target?.role || uploadCategory,
        subjectId: target?.role === 'packet' ? (target?.subject_id ?? null) : null,
        materialCategory: target?.role || uploadCategory,
        replacesMaterialId: materialId,
      })
      await refresh(taskId)
      await waitForParsing(taskId)
    } catch (e) {
      onError(`${file.name}：${e.message}`)
    } finally {
      setUploadsInFlight(0)
    }
  }, [materials, onError, refresh, taskId, uploadCategory, waitForParsing])

  const changeRole = async (materialId, role) => {
    if (!taskId) return
    try { await setBsMaterialRole(taskId, materialId, role); await refresh(taskId) } catch (e) { onError(e.message) }
  }

  const changeSubject = async (materialId, subjectId) => {
    if (!taskId || !subjectId) return
    try {
      await assignBsMaterialSubject(taskId, materialId, subjectId)
      await refresh(taskId)
    } catch (e) {
      onError(e.message)
    }
  }

  const removeMaterial = async (materialId) => {
    if (!taskId) return
    try { await deleteBsMaterial(taskId, materialId); await refresh(taskId) } catch (e) { onError(e.message) }
  }

  const onStartClick = async () => {
    if (!taskId || busy) return
    try {
      const d = await refresh(taskId) // 开始前取最新校验（开始时快照以服务端为准）
      setConfirmModal(d.validation)
    } catch (e) { onError(e.message) }
  }

  const confirmStart = async () => {
    if (submitting) return   // C3：防重复提交（按钮 disabled 只是视觉，此处是行为硬门）
    try {
      setSubmitting(true)
      const plan = detail?.supplement_plan
      const recoveredSupplement = plan?.status === 'planned'
        && plan?.generation_id != null
        && plan.generation_id === detail?.options_snapshot?.supplement_edit?.generation_id
      const res = (supplement?.supplementMode || recoveredSupplement)
        ? await activateBsSupplementPlan(taskId) : await startBsReview(taskId)
      setConfirmModal(null)
        // T10-C C3：后端受理为 `preparing`（202，受理体 `preparing:true`，**不带 ok**）时
        // 立即进入 Run 页面展示材料读取准备态；Run 页面不会开始五步核查，直到任务真实进入 running。
      if (res && res.preparing) {
        setSubmitting(false)
          onStarted(taskId)
        return
      }
      setSubmitting(false)
      onStarted(taskId)
    } catch (e) {
      setSubmitting(false)
      setConfirmModal(null)
      onError(e.message)
    }
  }

  // P2 原型上传页接管主路径：上传/启动动作仍复用同一组 API 回调。
  const prototypeTask = buildPrototypeTask({
    ...(detail || {}),
    state: detail?.state || 'setup',
    section: {
      project_name: section.projectName,
      contract_section: section.contractSection,
      baseline_date: section.baselineDate,
    },
  })
  return (
      <div data-testid="prototype-upload-stage" className="bs-v3 flex min-h-0 flex-1 flex-col px-0 py-0">
      {/* 四阶段流程条属于页面级外壳（原型 renderStepper 在页面级只渲染一次），
          不属于某个阶段内部，故不放在 PrototypeUploadPage 里。
          只读查看态可点回已到达的后续阶段；新建/修改材料阶段没有后续阶段可去。 */}
      <PrototypeFlowHeader
        taskState={detail?.state || 'setup'}
        stage="upload"
        reachedIndex={reachedIndex}
        onStageSelect={readOnly ? onStageSelect : undefined}
      />
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <PreviousSupplementReview key={`${taskId}-${detail?.options_snapshot?.supplement_edit?.previous_version_no}`} taskId={taskId} versionNo={detail?.options_snapshot?.supplement_edit?.previous_version_no} />
          {taskId && !detail ? (
            <p role={detailError ? 'alert' : 'status'} className="p-4 text-sm text-[#6B7280]">
              {detailError ? `材料加载失败：${detailError}` : '正在加载材料…'}
            </p>
          ) : <PrototypeUploadPage
          task={prototypeTask}
          readOnly={readOnly}
            preparing={isPreparing}
            parseEtaLabel={bsEtaLabel(etaRef.current, etaNow)}
          onRequestEdit={onRequestEdit}
          onExit={onExit}
          uploadRole={uploadCategory}
          uploadSubject={uploadSubject}
            onReset={onReset}
            resetting={resetting}
          subjectOptions={subjectOptions}
          busy={busy}
          supplement={showSupplement ? supplementView : null}
          onSupplementCancel={() => setSupplementDismissed(true)}
          onSectionChange={changeSection}
          onRoleChange={(role) => { setUploadCategory(role); if (role !== 'packet') setUploadSubject('') }}
          onSubjectChange={setUploadSubject}
          onMaterialRoleChange={changeRole}
          onMaterialSubjectChange={changeSubject}
          onSelectFiles={() => !busy && inputRef.current?.click()}
          onRemove={removeMaterial}
          onReplace={replaceMaterial}
          onStart={onStartClick}
        />}
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          accept={ACCEPT}
          onChange={(event) => { addFiles(event.target.files); event.target.value = '' }}
        />
      </div>
      {confirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6" data-testid="prototype-start-confirm">
          <div className="w-[min(540px,100%)] rounded-[10px] border border-[#E9E3D9] bg-white p-5 shadow-xl" data-testid="confirm-readiness">
            <h2 className="text-[17px] font-bold">{confirmModal.hard_ok && !confirmModal.parsing_pending ? '开始核查' : '暂不能开始核查'}</h2>
            {(confirmModal.hard_gaps || []).length > 0 && <ul className="mt-3 list-disc rounded-lg bg-[#FDECEA] px-5 py-3 text-[14px] text-[#C62828]">{confirmModal.hard_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
            <div className="mt-4 flex justify-end gap-2"><button type="button" className="rounded-lg border border-[#E9E3D9] px-3 py-2 text-[14px]" onClick={() => setConfirmModal(null)}>返回修改材料</button>{confirmModal.hard_ok && !confirmModal.parsing_pending ? <button type="button" className="rounded-lg bg-[#8B7355] px-4 py-2 text-[14px] font-bold text-white" onClick={confirmStart}>{submitting ? '提交中…' : '确认并开始核查'}</button> : null}</div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ==================== 第二段：核查运行 + 结论 + 报告（v5 证据溯源） ==================== */

/* SSE 事件流：EventSource 按事件名分发；断线由浏览器自动重连并携带
   Last-Event-ID 续传（后端流内 id 行同源），前端不做重连退避；
   done 帧主动 close 防终态后无限重连；keepalive 帧无 sequence 不进时间线；
   StrictMode 双挂载（开发环境 effect 跑两遍）靠 maxSeq 去重防重复渲染。 */
function useBsEventStream(taskId, runVersion) {
  const [events, setEvents] = useState([])
  const maxSeqRef = useRef(0)
  useEffect(() => {
    setEvents([])
    maxSeqRef.current = 0
    let es = null
    const onFrame = (e) => {
      let dto
      try { dto = JSON.parse(e.data) } catch { return }
      if (dto.sequence == null || dto.sequence <= maxSeqRef.current) return
      maxSeqRef.current = dto.sequence
      setEvents((prev) => [...prev, dto])
    }
    es = new EventSource(bsEventsUrl(taskId, maxSeqRef.current), { withCredentials: true })
    for (const t of BS_EVENT_TYPES) es.addEventListener(t, onFrame)
    es.addEventListener('stream.reset', () => { maxSeqRef.current = 0; setEvents([]) })
    es.addEventListener('done', () => es.close())
    // onerror 不处理：readyState=CONNECTING 时浏览器自动重试（带 Last-Event-ID）；
    // 永久失败（任务不存在/未登录）readyState=CLOSED，保留已收事件静默停止。
    return () => { if (es) es.close() }
  }, [taskId, runVersion])
  return events
}


// 报告技术前提 409 → 结构化面板数据（人工处置不属于此通道）。
function normalizeReportGate(error) {
  const detail = error?.detail
  const status = error?.status
  if (typeof detail === 'string' && detail.trim()) {
    // 只有 409 才是结构化覆盖提示；500/网络故障的字符串 detail 仍走错误横幅，
    // 不能套上「未处置/未完成」面板造成误判。
    if (status !== 409) return null
    return { code: null, message: detail.trim(), items: [], missingGroups: [], pendingGroups: [] }
  }
  if (!detail || typeof detail !== 'object') return null
  const blocking = detail.blocking && typeof detail.blocking === 'object' && !Array.isArray(detail.blocking)
    ? detail.blocking
    : null
  const items = Array.isArray(detail.blocking)
    ? detail.blocking
    : Array.isArray(blocking?.items) ? blocking.items : []
  const missingGroups = detail.missing_groups || detail.missingGroups || blocking?.missing_groups || blocking?.missingGroups || []
  const pendingGroups = detail.pending_groups || detail.pendingGroups || blocking?.pending_groups || blocking?.pendingGroups || []
  // 不再根据 blocking 数组推导人工门禁；只有后端显式返回的覆盖代码才进入面板。
  const code = detail.code || null
  if (status !== 409 && code == null && missingGroups.length === 0 && pendingGroups.length === 0) return null
  return {
    code,
    message: typeof detail.message === 'string' ? detail.message : (typeof error?.message === 'string' ? error.message : ''),
    items: items.filter((item) => item && typeof item === 'object'),
    missingGroups: Array.isArray(missingGroups) ? missingGroups : [],
    pendingGroups: Array.isArray(pendingGroups) ? pendingGroups : [],
  }
}

function WorkbenchStage({ taskId, autoRunOnMount = false, initialView = null, reachedIndex = 0,
  onPreparationNeedsAction,
    onReachStage, onStageSelect, onOpenMaterials, onRequestEditMaterials, onRequestSupplementMaterials, onOpenSupplementOverlay,
    onError, onEditMaterials, onReset, resetting = false, onOpenReviewNotes }) {
  const [detail, setDetail] = useState(null)
  const [findings, setFindings] = useState([])
  const [publication, setPublication] = useState(null)
  const publicationPending = detail?.supplement_plan?.status === 'running'
    && (publication?.generationId !== detail.supplement_plan.generation_id || publication?.status !== 'ready')
  const [referenceSnapshot, setReferenceSnapshot] = useState(null)
  // 初值 true：挂载即拉 findings，若默认 false 会先渲染一帧「暂无核查结论」再被数据替换（假空态闪现）
  const [findingsLoading, setFindingsLoading] = useState(true)
  // T8-U5-R1A Task 2：结论拉取失败原因（显式失败通道，与「暂无核查结论」区分）
  const [findingsError, setFindingsError] = useState('')
  const [runningKey, setRunningKey] = useState(null)
  const [autoCheck, setAutoCheck] = useState({ phase: 'idle', index: 0, total: 0, error: '' })
  const autoRunRef = useRef(false)
  const aiResumeSeenRef = useRef(null)
  // 当前审查代（服务端唯一身份）：本地编排状态的读/写都以它为准。
  const reviewRunId = referenceSnapshot?.review_run_id ?? null
  // ── 报告两稿别（设计 100 §5）──
  // stage=draft：AI 初稿；stage=confirmed：确认稿。两稿别各自缓存、各自记录技术前提
  // 错误，人工未处置不作为报告资格；读取/导出都绑定当前组件、运行代及依据代。
  const [reportStage, setReportStage] = useState('draft')
  const [reports, setReports] = useState({ draft: null, confirmed: null })
  const [opinion, setOpinion] = useState(null)
  const [opinionMarkdown, setOpinionMarkdown] = useState(null)
  const opinionRequest = useRef(0)
  const [opinionLoading, setOpinionLoading] = useState(false)
  const [opinionError, setOpinionError] = useState('')
  const [opinionExporting, setOpinionExporting] = useState(null)
  const [reportGates, setReportGates] = useState({ draft: null, confirmed: null })
  const [reportStageErrors, setReportStageErrors] = useState({ draft: '', confirmed: '' })
  const [reportLoading, setReportLoading] = useState({ draft: false, confirmed: false })
  const [confirmingEmployer, setConfirmingEmployer] = useState(false)
  const [reportRefreshMessage, setReportRefreshMessage] = useState('')
  const reportContext = useRef({ active: true, epoch: 0, runId: null })
  // 两稿别各自的请求代，切换稿别/重核查时互不干扰（旧响应不得覆盖新稿别）。
  const reportRequests = useRef({ draft: 0, confirmed: 0 })
  // 概览页的材料未读页/合同要素沿用 AI 初稿口径（确认稿不外溢到概览）。
  const report = reports.draft
  reportContext.current.runId = referenceSnapshot?.review_run_id ?? null
  useEffect(() => {
    reportContext.current.active = true
    return () => { reportContext.current.active = false }
  }, [])
  const reportIsCurrent = useCallback((context) => reportContext.current.active
    && context.epoch === reportContext.current.epoch && context.runId === reportContext.current.runId, [])
  const [versionDetail, setVersionDetail] = useState(null)
  const [versionLoading, setVersionLoading] = useState(false)
  // 刀B（BS-P2-04）：规则集版本视图。读取失败必须显式「暂不可用」，不得伪造无变化/0 条。
  const [ruleset, setRuleset] = useState(null)
  const [rulesetLoading, setRulesetLoading] = useState(false)
  const [rulesetError, setRulesetError] = useState('')
  // 规则级重跑的结果提示（成功/失败都落在规则集提示区内，不另起全局横幅）。
  const [rulesetRerunStatus, setRulesetRerunStatus] = useState('')
  const [rulesetRerunDetail, setRulesetRerunDetail] = useState('')
  const [rulesetRerunError, setRulesetRerunError] = useState('')
  const [previewRequest, setPreviewRequest] = useState(null)
  const [activeView, setActiveViewState] = useState(() => {
    // 从材料查看态按流程条回来时，落回用户离开的那一屏。
    if (['run', 'review', 'report', 'materials'].includes(initialView)) return initialView
    try {
      const requested = new URLSearchParams(window.location.search).get('bs_view')
      return ['overview', 'run', 'review', 'report', 'materials'].includes(requested)
        ? requested
        : (autoRunOnMount ? 'run' : 'overview')
    } catch { return autoRunOnMount ? 'run' : 'overview' }
  })
  const manualViewRef = useRef(false)
  const [materialsOrigin, setMaterialsOrigin] = useState(() => {
    try {
      const origin = new URLSearchParams(window.location.search).get('bs_material_origin')
      return ['overview', 'review', 'report'].includes(origin) ? origin : 'overview'
    } catch { return 'overview' }
  })
  const setActiveView = useCallback((next, options = {}) => {
    manualViewRef.current = true
    setActiveViewState(next)
    try {
      const params = new URLSearchParams(window.location.search)
      if (next && next !== 'overview') params.set('bs_view', next)
      else params.delete('bs_view')
      if (next === 'materials') {
        const origin = options.materialsOrigin || materialsOrigin
        if (['overview', 'review', 'report'].includes(origin)) params.set('bs_material_origin', origin)
      } else {
        params.delete('bs_material_origin')
      }
      const query = params.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    } catch { /* non-browser test host */ }
  }, [materialsOrigin])
  useEffect(() => {
    if (publicationPending && ['overview', 'review', 'report'].includes(activeView)) setActiveView('run')
  }, [publicationPending, activeView, setActiveView])
  const [completing, setCompleting] = useState(false)
  const [runVersion, setRunVersion] = useState(0)
  const events = useBsEventStream(taskId, runVersion)
  // 首次回放历史事件只建立刷新基线；只有后续 SSE sequence 增长才重新读取任务详情。
  // 这样不会把「事件历史首载」误当成新事件，避免生产预览中重复 GET /tasks/{id}。
  const eventReloadBaselineRef = useRef({ taskId: null, runVersion: null, sequence: null, suppressUntil: 0 })

  // findings 与 detail 分开刷新：复核回执/重核查只需刷结论，不必等 get_task；
  // 也让「运行核查」这类长耗时动作（后端同步跑 A1/A2/A3，可达数十秒）不阻塞三栏结论。
  // T8-U5-R1A Task 2：findings 请求失败必须**显式失败**。此前用裸 fetch + r.json()，
  // 401/403/500 会静默解析成空体 → 被渲染成「暂无核查结论」，把故障伪装成「核查无结论」。
  // 现在走 listBsFindings（内部 _json：非 2xx 抛后端 detail 或平实 fallback），
  // 失败时**保留已有 findings**（不清空为 []），并把原因同时交给 onError（顶部横幅）与
  // 三栏中栏的 error 分支呈现；loading 一律在 finally 收口，绝不停在半路。
  // 刀A（BS-P1-06）：待办聚合（缺件按材料类归并、可补件判断与计数）由后端 `/todo` 唯一给出。
  // 读取失败必须显式呈现（面板显示「暂不可用」），不得把故障伪装成「没有缺件」。
  const [todo, setTodo] = useState(null)
  const [todoLoading, setTodoLoading] = useState(false)
  const [todoError, setTodoError] = useState('')
  const todoRequest = useRef(0)
  const loadTodo = useCallback(async () => {
    const request = ++todoRequest.current
    setTodoLoading(true)
    try {
      const data = await getBsTodo(taskId)
      if (request !== todoRequest.current) return
      setTodo(data || null)
      setTodoError('')
    } catch (e) {
      if (request !== todoRequest.current) return
      // 保留已有清单（若有），只标出这次读取失败。
      setTodoError(e.message)
    } finally {
      if (request === todoRequest.current) setTodoLoading(false)
    }
  }, [taskId])

  const [materialConfirming, setMaterialConfirming] = useState(false)
  const confirmMaterial = useCallback(async (findingId, decision, token, expectedConfirmationId, materialId) => {
    if (materialConfirming) return
    setMaterialConfirming(true)
    try {
      await confirmBsMaterial(taskId, findingId, decision, token, expectedConfirmationId, materialId)
      // 确认改变报告输入，同一审查代中尚未返回的旧报告也不能再覆盖新状态。
      reportContext.current.epoch += 1
      reportRequests.current = { draft: reportRequests.current.draft + 1, confirmed: reportRequests.current.confirmed + 1 }
      opinionRequest.current += 1
      setReports({ draft: null, confirmed: null })
      setReportLoading({ draft: false, confirmed: false })
      setReportGates({ draft: null, confirmed: null })
      setReportStageErrors({ draft: '', confirmed: '' })
      setReportRefreshMessage('')
      setOpinion(null)
      setOpinionMarkdown(null)
      setOpinionLoading(false)
      setOpinionExporting(null)
      setOpinionError('')
    } catch (e) {
      onError(e.message || '材料确认失败，请重试')
    } finally {
      await loadTodo()
      setMaterialConfirming(false)
    }
  }, [taskId, materialConfirming, loadTodo, onError])

  const loadFindings = useCallback(async () => {
    setFindingsLoading(true)
    try {
      const data = await listBsFindings(taskId)
      setFindings(data?.findings || [])
      setPublication({ status: data?.publication_status, generationId: data?.publication_generation_id })
      setReferenceSnapshot(data?.reference_snapshot ?? null)
      setFindingsError('')
      // 缺件聚合与结论列表同源（同一任务、同一审查代），随结论一起刷新。
      loadTodo()
      return data
    } catch (e) {
      setFindingsError(e.message)
      onError(e.message)
      return null
    } finally { setFindingsLoading(false) }
  }, [taskId, onError, loadTodo])

  const reload = useCallback(async () => {
    try {
      const d = await getBsTask(taskId)
      setDetail(d)
    } catch (e) { onError(e.message) }
  }, [taskId, onError])

  useEffect(() => {
    // 准备可能在首次详情读取之前就失败，不能只依赖观察到 preparing 后的轮询。
    if (autoRunOnMount && detail?.state === 'setup') onPreparationNeedsAction?.(taskId)
  }, [autoRunOnMount, detail?.state, onPreparationNeedsAction, taskId])

    // preparing 属于 Run 页面：后台读取正文/OCR/判章期间持续刷新任务详情，
    // 进入 running 后由自动核查计划 effect 接管五步编排；失败/回退状态保持真实展示。
    useEffect(() => {
      if (!taskId || detail?.state !== 'preparing') return undefined
      let dead = false
      const poll = async () => {
        try {
          const next = await getBsTask(taskId)
          if (!dead && next.state === 'setup') {
            onPreparationNeedsAction?.(taskId)
            return
          }
          // Preparing may still expose the previous generation's findings. Reload
          // them before planning work for the newly activated generation.
          if (next.state === 'running' && await loadFindings() === null) return
          if (!dead) setDetail(next)
        } catch (e) {
          if (!dead) onError(e.message)
        }
      }
      const timer = setInterval(poll, 1500)
      poll()
      return () => { dead = true; clearInterval(timer) }
    }, [taskId, detail?.state, onError, onPreparationNeedsAction, loadFindings])

  // SSE 事件到达后短暂去抖刷新任务进度回执。SSE 负责实时唤醒，GET /tasks/{id}
  // 负责返回同一事务视图下的权威 unit 计数，前端不再按事件数量自行估算百分比。
  const lastEventSequence = events.at(-1)?.sequence ?? null
  useEffect(() => {
    const baseline = eventReloadBaselineRef.current
    if (!taskId || baseline.taskId !== taskId || baseline.runVersion !== runVersion) {
      eventReloadBaselineRef.current = {
        taskId,
        runVersion,
        sequence: null,
        // SSE 首次回放可能连续送出历史事件；给首载窗口留出收敛时间，
        // 避免把历史回放误当成新事件触发第二次任务详情 GET。
        suppressUntil: Date.now() + 600,
      }
      return undefined
    }
    if (lastEventSequence == null) return undefined
    if (Date.now() < baseline.suppressUntil) {
      baseline.sequence = lastEventSequence
      return undefined
    }
    if (baseline.sequence == null) {
      baseline.sequence = lastEventSequence
      return undefined
    }
    if (lastEventSequence <= baseline.sequence) return undefined
    baseline.sequence = lastEventSequence
    const timer = setTimeout(() => { reload() }, 120)
    return () => clearTimeout(timer)
  }, [lastEventSequence, reload, runVersion, taskId])

  const loadVersion = useCallback(async (versionNo) => {
    setVersionLoading(true)
    try {
      setVersionDetail(await getBsReviewVersion(taskId, versionNo))
    } catch (e) {
      onError(e.message)
    } finally {
      setVersionLoading(false)
    }
  }, [taskId, onError])

  // 规则集版本只在挂载与收口后刷新：它表达「当前规则集 vs 本任务结论所用规则集」，
  // 不随每轮 findings 轮询变化；失败保留显式错误状态而不是清成「无变化」。
  const loadRuleset = useCallback(async () => {
    setRulesetLoading(true)
    try {
      setRuleset(await getBsRuleset(taskId))
      setRulesetError('')
    } catch (e) {
      setRuleset(null)
      setRulesetError(e.message)
    } finally {
      setRulesetLoading(false)
    }
  }, [taskId])

  useEffect(() => { reload(); loadFindings(); loadRuleset() }, [reload, loadFindings, loadRuleset])

  // 把「任务真实走到哪一阶段」上报给页面级流程条，作为可点性的下限；
  // 流程条自身不再按「当前所在阶段」倒推可点性（否则回看上一阶段会锁死后续）。
  useEffect(() => {
    if (!detail?.state) return
    onReachStage?.(stageIndexOf(stageFromTaskState(detail.state)))
  }, [detail?.state, onReachStage])

  // 复核期间后端仍处于 running；记住实际打开的复核页，返回材料查看后才能回到复核。
  useEffect(() => {
    if (activeView === 'review') onReachStage?.(stageIndexOf('review'))
  }, [activeView, onReachStage])
  const markReviewReady = useCallback(() => onReachStage?.(stageIndexOf('review')), [onReachStage])

  // 原型阶段是真正的主路由：刷新运行中/待复核/已完成任务时直接回到对应屏，
  // 不再落到旧版「概览」标签，避免用户看到与任务状态不一致的页面。
  useEffect(() => {
    if (!detail?.state) return
    const requested = (() => {
      try { return new URLSearchParams(window.location.search).get('bs_view') } catch { return null }
    })()
    if (requested && ['overview', 'materials', 'run', 'review', 'report'].includes(requested)) return
    if (manualViewRef.current) return
    if (detail.state === 'running' && activeView === 'overview') setActiveView('run')
    if (detail.state === 'review' && activeView === 'overview') setActiveView('review')
    if (detail.state === 'done' && activeView === 'overview') setActiveView('report')
  }, [activeView, detail?.state, setActiveView])

  // ── 三栏工作台接线（T8-U5A Task 1）：view-model + 选择态 + 只读 flags 全部来自 U2，入口不重复派生 ──
  // hook 在 handleReview 之后调用（见下），此处仅声明证据打开闭环。

  // 证据打开闭环（T8-U5A Task 3）：仅 precise 档 + 材料存在时才有入口（U2 的 actions.canOpenSource 已把关）；
  // 页码直接用后端原始 page，不做任何前端换算。page/unresolved/空定位/材料不存在一律走不到这里。
  const openSource = useCallback((materialId, page, findingId = null) => {
    setPreviewRequest({ materialId, page: page || null, findingId })
  }, [])

  const autoChecksForScope = useCallback((taskScope = detail?.scope, rerunGroups = undefined) => {
    const scope = taskScope || {}
    const enabled = AUTO_CHECK_ORDER.filter((key) => scope[AUTO_CHECK_SCOPE[key]])
    if (rerunGroups === undefined) return enabled
    if (!Array.isArray(rerunGroups)) throw new Error('重跑返回的核查组格式无效，请刷新后重试')

    const unknown = [...new Set(rerunGroups.filter((groupId) => !AUTO_CHECK_GROUPS.has(groupId)))]
    if (unknown.length) throw new Error(`重跑返回未知核查组：${unknown.join('、')}`)

    const enabledGroups = new Set(enabled.map((key) => AUTO_CHECK_GROUP[key]))
    const disabled = [...new Set(rerunGroups.filter((groupId) => !enabledGroups.has(groupId)))]
    if (disabled.length) throw new Error(`重跑范围包含当前任务未启用的核查组：${disabled.join('、')}`)

    const requested = new Set(rerunGroups)
    return enabled.filter((key) => requested.has(AUTO_CHECK_GROUP[key]))
  }, [detail?.scope])

  // 服务端真相：还有哪些核查组没跑完（与编排循环内的完成判据同一口径）。
  // sessionStorage 只按标签页会话存活，重新登录后会丢失 ⇒ 恢复不能只依赖它。
  const pendingAutoSteps = useMemo(
    () => pendingAutoCheckSteps({
      taskState: detail?.state,
      steps: autoChecksForScope(),
      runs: referenceSnapshot?.runs || [],
      groupOf: (key) => AUTO_CHECK_GROUP[key],
    }),
    [autoChecksForScope, detail?.state, referenceSnapshot],
  )

  // reportError=false：由调用方（规则集局部重跑）在提示区内呈现失败原因，
  // 不再同时弹顶部横幅——同一句故障文案只留一处 DOM。
  const runAutoChecks = useCallback(async ({
    reportError = true,
    rerunGroups = undefined,
    recoverRerunGroups = true,
    retryAi = false,
  } = {}) => {
    if (autoRunRef.current || !taskId) return { ok: false, skipped: true }

    const rerunSnapshot = detail?.options_snapshot?.rerun
    const recoveredGroups = recoverRerunGroups
      && rerunSnapshot
      && Object.prototype.hasOwnProperty.call(rerunSnapshot, 'rerun_groups')
      ? rerunSnapshot.rerun_groups
      : undefined
    const effectiveGroups = rerunGroups !== undefined ? rerunGroups : recoveredGroups
    let steps
    try {
      steps = autoChecksForScope(detail?.scope, effectiveGroups)
    } catch (e) {
      const message = e?.message || '重跑范围无法识别，请刷新后重试'
      writeAutoRunStatus(taskId, reviewRunId, 'failed')
      setAutoCheck({ phase: 'failed', index: 0, total: 0, error: message })
      if (reportError) onError(message)
      return { ok: false, error: message }
    }
    if (!steps.length) {
      writeAutoRunStatus(taskId, reviewRunId, 'completed')
      setAutoCheck({ phase: 'done', index: 0, total: 0, error: '' })
      await loadRuleset()
      return { ok: true, steps: 0 }
    }

    autoRunRef.current = true
    setRunningKey('auto')
    setAutoCheck({ phase: 'running', index: 0, total: steps.length, error: '' })
    writeAutoRunStatus(taskId, reviewRunId, 'running')

    let doingAi = false
    let runId = reviewRunId
    try {
      let result = await loadFindings()
      if (result === null) {
        throw new Error('核查结果读取失败，请点击“重试核查”')
      }
      // 这一代的服务端身份以最近一次 findings 响应为准；后续写状态都用它，
      // 避免整轮编排过程中因快照刷新把上下文写向上一代。
      runId = result?.reference_snapshot?.review_run_id ?? reviewRunId
      const knownKinds = new Set((result?.findings || []).map((finding) => finding.kind))
      for (let index = 0; index < steps.length; index += 1) {
        const key = steps[index]
        setAutoCheck({ phase: 'running', index, total: steps.length, error: '' })
        const requiredKinds = AUTO_CHECK_KINDS[key] || []
        const groupId = AUTO_CHECK_GROUP[key]
        const completed = result?.reference_snapshot
          ? (result.reference_snapshot.runs || []).some((run) => run.group_id === groupId && run.state === 'completed')
          : requiredKinds.length && requiredKinds.every((kind) => knownKinds.has(kind))
        if (completed) continue
        await runBsCheck(taskId, groupId)
        result = await loadFindings()
        if (result === null) {
          throw new Error('核查结果读取失败，请点击“重试核查”')
        }
        for (const finding of result?.findings || []) knownKinds.add(finding.kind)
        await reload()
      }
      const snapshot = result?.reference_snapshot
      let aiWarning = false
      let unlocatedScopeCount = 0
      if (snapshot?.ai_auto_enabled === true) {
        doingAi = true
        aiResumeSeenRef.current = snapshot.review_run_id
        if (!Array.isArray(snapshot.ai_auto_groups)) throw new Error('AI 核查范围无效，请刷新后重试')
        const aiResult = await runBackgroundAiReview({
          taskId, reviewRunId: snapshot.review_run_id,
          startJob: startBsBackgroundAiReview, getJob: getBsBackgroundAiReview, retry: retryAi,
          onProgress: (groupId) => setAutoCheck({ phase: 'running', stage: 'ai',
            index: Math.max(0, snapshot.ai_auto_groups.indexOf(groupId)), total: snapshot.ai_auto_groups.length,
            groupId, activity: 'status', error: '' }),
        })
        if (aiResult.state === 'pending') {
          aiResumeSeenRef.current = snapshot.review_run_id
          setAutoCheck({ phase: 'waiting', stage: 'ai', index: 0, total: snapshot.ai_auto_groups.length, error: '' })
          return { ok: false, pending: true }
        }
        if (aiResult.state === 'failed') {
          const labels = { timeout: '模型响应超时', invalid_response: '模型返回格式无效', input_budget: '输入超过处理范围', model_call_error: '模型调用异常', rate_limit: '模型服务请求受限（HTTP 429）', authentication: '模型服务认证、权限或额度异常', service_error: '模型服务暂时不可用' }
          const reasons = (aiResult.failureCategories || []).map((code) => labels[code]).filter(Boolean)
          throw new Error(`部分 AI 辅助核查未完成${reasons.length ? `（${reasons.join('、')}）` : ''}，请重试或人工核实`)
        }
        if (aiResult.state === 'interrupted') throw new Error('后台核查已中断，已完成结果已保留；请确认服务正常后重试')
        aiWarning = aiResult.warning
        unlocatedScopeCount = aiResult.unlocatedScopeCount || 0
        // 模型候选可能新增复核问题；收口前读取同一核查代的最新清单。
        if (await loadFindings() === null) {
          throw new Error('核查结果读取失败，请点击“重试核查”')
        }
      }
      writeAutoRunStatus(taskId, runId, 'completed')
      // Plus 任务收口：通知顶栏积分徽标与积分页刷新（与 AS/Chat 同一事件）。
      window.dispatchEvent(new CustomEvent('billing:mutated'))
      setAutoCheck({ phase: 'done', index: steps.length, total: steps.length, error: '',
        warning: unlocatedScopeCount ? `有 ${unlocatedScopeCount} 项未定位合同分包范围，请补充材料或在人工复核中逐项核实` : aiWarning ? '部分核查项的依据、材料或 AI 建议需要人工核实' : '' })
      // AI 结果在最后一个批次落库，刷新权威进度，避免沿用重试前的失败状态。
      await reload()
      await loadRuleset()
      return { ok: true, steps: steps.length }
    } catch (e) {
      const message = e?.message || '核查执行失败，请重试'
      writeAutoRunStatus(taskId, runId, 'failed')
      // 自动核查失败收口：已产生的消耗也要让顶栏/积分页刷新。
      window.dispatchEvent(new CustomEvent('billing:mutated'))
      setAutoCheck({ phase: 'failed', stage: doingAi ? 'ai' : null, index: 0, total: steps.length, error: message,
        recheckRequired: doingAi && aiReviewRequiresNewRun(e) })
      if (reportError) onError(message)
      return { ok: false, error: message }
    } finally {
      autoRunRef.current = false
      setRunningKey(null)
    }
  }, [autoChecksForScope, detail?.options_snapshot, detail?.scope, loadFindings, loadRuleset, onError, reload, reviewRunId, taskId])

  const retryAutoChecks = useCallback(() => {
    setFindingsError('')
    onError('')
    runAutoChecks({ retryAi: true })
  }, [onError, runAutoChecks])

  // 启动与续跑共用同一判据（lib/autoCheckResume.planAutoChecks）：
  // 不再用「本地标记」和「服务端真相」两套互斥分支，避免材料改动换代后
  // 旧代 completed 把新一代永久挡住（2026-10-29 任务 77）。
  useEffect(() => {
    if (findingsLoading || !detail) return
    // 已有编排在跑（含本次 rerunTask 主动发起的）时不再并发启动一轮：
    // 否则主动调用会因 autoRunRef 互斥而空转返回，把「已提交」误判成失败。
    if (runningKey !== null) return
    const plan = planAutoChecks({
      taskState: detail.state,
      steps: autoChecksForScope(),
      runs: referenceSnapshot?.runs || [],
      localStatus: readAutoRunStatus(taskId, reviewRunId),
      autoRun: autoRunOnMount,
      hasFindings: findings.length > 0,
      groupOf: (key) => AUTO_CHECK_GROUP[key],
    })
    if (plan.action !== 'none') runAutoChecks()
    else if (detail.state === 'running' && referenceSnapshot?.ai_auto_enabled === true &&
             Array.isArray(referenceSnapshot.ai_auto_groups) && referenceSnapshot.ai_auto_groups.length > 0 &&
             referenceSnapshot.ai_auto_groups.every((group) => (referenceSnapshot.runs || []).some(
               (run) => run.group_id === group && run.state === 'completed')) &&
             aiResumeSeenRef.current !== reviewRunId) {
      aiResumeSeenRef.current = reviewRunId
      runAutoChecks()
    }
  }, [autoChecksForScope, autoRunOnMount, detail, findings.length, findingsLoading,
    referenceSnapshot, reviewRunId, runAutoChecks, taskId, runningKey])

  useEffect(() => {
    if (autoCheck.phase !== 'waiting' || autoCheck.stage !== 'ai') return undefined
    const timer = setTimeout(() => runAutoChecks(), 5000)
    return () => clearTimeout(timer)
  }, [autoCheck.phase, autoCheck.stage, runAutoChecks])

  // 收口任务（P0-2）：全部分组均有结论才允许 → state=done，后端并发 task.completed 事件；
  // 缺组时后端 409 + detail，由 onError 呈现（文案已说明缺哪些分组）。
  // 刀B：收口成功后刷新规则集视图——此时后端才写 ruleset_version，更新提示应随之消失/更新。
  const completeTask = async () => {
    try {
      setCompleting(true)
      await completeBsTask(taskId)
      // 手动收口成功：本任务此前已产生消耗，刷新顶栏/积分页。
      window.dispatchEvent(new CustomEvent('billing:mutated'))
      await loadFindings()
      await reload()
      await loadRuleset()
      setActiveView('report')
    } catch (e) { onError(e.message) } finally { setCompleting(false) }
  }

  // 刀B（BS-P1-13）：重核查支持可选局部范围。默认（无参数）保持旧行为 = 全量重跑；
  // 规则集提示区传入 { scope:'items', itemIds }，由后端按范围过滤落库并继承未变回执。
  // statusKey='ruleset' 时成功/失败落在规则集提示区，不再弹顶部横幅（避免同文案两处）。
  const rerunTask = async ({ scope = null, itemIds = null, statusKey = 'manual' } = {}) => {
    if (!taskId || runningKey !== null || detail?.state === 'setup' || detail?.state === 'preparing') return false
    if (statusKey === 'ruleset') {
      setRulesetRerunStatus('')
      setRulesetRerunDetail('')
      setRulesetRerunError('')
    }
    try {
      reportContext.current.epoch += 1
      reportRequests.current = { draft: reportRequests.current.draft + 1, confirmed: reportRequests.current.confirmed + 1 }
      setReports({ draft: null, confirmed: null })
      setOpinion(null)
      setOpinionMarkdown(null)
      setOpinionError('')
      setOpinionExporting(null)
      setReportGates({ draft: null, confirmed: null })
      setReportStageErrors({ draft: '', confirmed: '' })
      setReportLoading({ draft: false, confirmed: false })
      setReportRefreshMessage('')
      setExporting(null)
      if (statusKey !== 'ruleset') setActiveView('run')
      setRunningKey('auto')
      setFindingsError('')
      setAutoCheck({ phase: 'resetting', index: 0, total: autoChecksForScope().length, error: '' })
      setRunVersion((value) => value + 1)
      writeAutoRunStatus(taskId, reviewRunId, 'running')
      let rerunResult
      if (scope === 'items' && Array.isArray(itemIds) && itemIds.length) {
        rerunResult = await rerunBsTask(taskId, { scope, itemIds })
      } else {
        // 无登记清单（或用户点「重新核查」）→ 全量重跑，保持旧 POST 形状（无请求体）。
        rerunResult = await rerunBsTask(taskId)
      }
      // 新一轮已受理，旧核查代产生的顶部错误不再适用。
      onError('')
      const hasRerunGroups = rerunResult != null
        && Object.prototype.hasOwnProperty.call(rerunResult, 'rerun_groups')
      const rerunGroups = hasRerunGroups ? rerunResult.rerun_groups : undefined
      // done 任务在重置受理前仍可能返回完成帧；受理后再连接，确保订阅新一轮。
      setRunVersion((value) => value + 1)
      await reload()
      const progressTotal = Array.isArray(rerunGroups) ? rerunGroups.length : autoChecksForScope().length
      setAutoCheck({ phase: 'running', index: 0, total: progressTotal, error: '' })
      const outcome = await runAutoChecks({
        reportError: statusKey !== 'ruleset',
        rerunGroups,
        // 当前 /rerun 响应缺少字段代表旧后端，必须兼容为完整 scope，不能误用旧详情快照。
        recoverRerunGroups: false,
      })
      if (statusKey === 'ruleset') {
        if (outcome?.pending) {
          setRulesetRerunStatus('规则核查已完成，正在等待 AI 辅助核查')
        } else if (outcome?.ok === false) {
          setRulesetRerunError(`重跑已提交，但自动核查未完成：${outcome.error || '请重试'}`)
        } else {
          const actualItemIds = Array.isArray(rerunResult?.item_ids) ? rerunResult.item_ids : itemIds
          const carriedGroups = Array.isArray(rerunResult?.carried_forward_groups)
            ? rerunResult.carried_forward_groups
            : []
          const fallbackReason = rerunResult?.fallback_reason
          setRulesetRerunDetail(
            fallbackReason
              ? (fallbackReason === 'baseline_date_changed' ? '核查基准日已更新，已改为全量重新核查' : fallbackReason === 'section_information_changed' ? '已补充项目或标段信息，已改为全量重新核查' : `无法安全局部重跑，已回退全量：${fallbackReason}`)
              : carriedGroups.length
                ? `新 generation 已显式沿用：${carriedGroups.join('、')}`
                : '新 generation 未发现沿用组'
          )
          setRulesetRerunStatus(scope === 'items' && Array.isArray(actualItemIds) && actualItemIds.length
            ? `已按新规则重跑 ${actualItemIds.length} 条规则`
            : '已按新规则全量重跑')
        }
      }
      return true
    } catch (e) {
      if (statusKey === 'ruleset') setRulesetRerunError(`重跑失败：${e.message}`)
      else onError(e.message)
      writeAutoRunStatus(taskId, reviewRunId, 'failed')
      setAutoCheck({ phase: 'failed', index: 0, total: 0, error: e.message })
      setRunningKey(null)
      return false
    }
  }

  // 报告完整性由后端只读闸判断；不调用 complete，也不以未复核问题阻塞读取。
  // 技术前提不足时落到 reportGates（结构化面板），不弹网络错误；其他失败才提示。
  const loadReport = useCallback(async (stage = 'draft', refresh = false) => {
    const context = { ...reportContext.current }
    const request = reportRequests.current[stage] + 1
    reportRequests.current[stage] = request
    const current = () => reportIsCurrent(context) && request === reportRequests.current[stage]
    setReportLoading((prev) => ({ ...prev, [stage]: true }))
    setReportStageErrors((prev) => ({ ...prev, [stage]: '' }))
    try {
      const result = await getBsReport(taskId, { stage })
      if (!current()) return
      if (context.runId && result.reference_snapshot?.review_run_id !== context.runId) {
        setReports((prev) => ({ ...prev, [stage]: null }))
        setReportGates((prev) => ({ ...prev, [stage]: null }))
        setReportStageErrors((prev) => ({ ...prev, [stage]: '本轮核查结果已更新，请重新读取报告。' }))
        return
      }
      setReports((prev) => ({ ...prev, [stage]: result }))
      setReportGates((prev) => ({ ...prev, [stage]: null }))
      setReportRefreshMessage(refresh ? '报告已按当前核查结果刷新；如没有新的处置或核查结果，内容不会变化。' : '')
    } catch (e) {
      if (!current()) return
      const gate = normalizeReportGate(e)
      setReports((prev) => ({ ...prev, [stage]: null }))
      setReportGates((prev) => ({ ...prev, [stage]: gate || null }))
      setReportStageErrors((prev) => ({ ...prev, [stage]: gate ? '' : (e.message || '报告暂时无法读取。') }))
      setReportRefreshMessage('')
      if (!gate) onError(e.message)
    } finally { if (current()) setReportLoading((prev) => ({ ...prev, [stage]: false })) }
    // 不把 onError 放进依赖：父级内联回调会每次渲染换引用，依赖它会让读取 effect 无限重跑。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, reportIsCurrent])

  useEffect(() => {
    if (!detail || ['setup', 'preparing'].includes(detail.state)) return
    // 抬头的合同金额/签订日期来自同一份报告要素抽取；人工复核页也要读取
    // 报告只读投影，否则只在进入报告页后才出现完整抬头，造成页面信息不一致。
    if (activeView === 'review' || activeView === 'report' || detail.state === 'done') loadReport(reportStage)
  }, [activeView, detail?.state, referenceSnapshot?.review_run_id, runningKey, reportStage, loadReport])

  // 原型主路径提供 Word/PDF；旧 stage 参数继续兼容后端历史接口。
  const [exporting, setExporting] = useState(null)
  const [versionExporting, setVersionExporting] = useState(null)
  const refreshReport = () => loadReport(reportStage, true)
  const confirmEmployer = async (candidate) => {
    if (!taskId || confirmingEmployer) return
    setConfirmingEmployer(true)
    try {
      await confirmBsEmployer(taskId, candidate)
      setReports({ draft: null, confirmed: null })
      await reload()
      if (!['setup', 'preparing'].includes(detail?.state)) await loadReport(reportStage, true)
    } catch (e) {
      onError(e.message)
      await reload()
    } finally { setConfirmingEmployer(false) }
  }
  const generateOpinion = async () => {
    if (!taskId || opinionLoading) return
    const context = { ...reportContext.current }
    const request = ++opinionRequest.current
    const current = () => reportIsCurrent(context) && request === opinionRequest.current
    const stage = 'confirmed'
    setOpinionLoading(true)
    setOpinionError('')
    try {
      const [opinionData, preview] = await Promise.all([
        getBsOpinion(taskId, { stage }), previewBsOpinion(taskId, stage),
      ])
      if (!current()) return
      if (!preview.digest || opinionData.opinion_digest !== preview.digest) throw new Error('意见书内容已变化，请重新生成预览')
      setOpinion(opinionData)
      setOpinionMarkdown(preview.markdown)
    } catch (e) {
      if (!current()) return
      setOpinion(null)
      setOpinionMarkdown(null)
      setOpinionError(e.message || '正式意见书暂时无法读取')
    } finally {
      if (current()) setOpinionLoading(false)
    }
  }
  const exportOpinion = async (fmt = 'docx') => {
    if (!taskId || opinionExporting) return
    const context = { ...reportContext.current }
    try {
      setOpinionExporting(fmt)
      const blob = await downloadBsOpinion(taskId, fmt, 'confirmed', { expectedDigest: opinion?.opinion_digest })
      if (reportIsCurrent(context)) saveBlob(blob, `${detail?.title || '分包合规审查正式意见书'}-${detail?.baseline_date || '核查'}.${fmt === 'pdf' ? 'pdf' : 'docx'}`)
    } catch (e) {
      if (reportIsCurrent(context)) onError(e.message || '正式意见书导出失败')
    } finally { if (reportIsCurrent(context)) setOpinionExporting(null) }
  }
  const changeReportStage = (stage) => {
    if (stage !== 'draft' && stage !== 'confirmed') return
    opinionRequest.current += 1
    setOpinionLoading(false)
    setOpinion(null)
    setOpinionMarkdown(null)
    setReportStage(stage)
  }
  const exportReport = async (stageOrFmt = 'docx', requestedFmt) => {
    const context = { ...reportContext.current }
    const isStage = stageOrFmt === 'draft' || stageOrFmt === 'confirmed'
    const stage = requestedFmt ? stageOrFmt : (isStage ? stageOrFmt : reportStage)
    const fmt = requestedFmt || (isStage ? 'docx' : stageOrFmt)
    if (reportGates[stage]) return
    try {
      setExporting(fmt)
      const name = detail?.title || '分包报审智能审查意见书'
      const blob = await downloadBsReport(taskId, fmt, stage)
      if (reportIsCurrent(context)) saveBlob(blob, `${name}-${detail?.baseline_date || '核查'}.${fmt === 'pdf' ? 'pdf' : 'docx'}`)
    } catch (e) {
      if (!reportIsCurrent(context)) return
      const gate = normalizeReportGate(e)
      if (gate) setReportGates((prev) => ({ ...prev, [stage]: gate }))
      else onError(e.message)
    } finally { if (reportIsCurrent(context)) setExporting(null) }
  }
  const downloadReference = async (reference, versionNo) => {
    const blob = await downloadBsReferenceFile(taskId, reference.reference_id, { versionNo })
    const name = (reference.doc_title || '规范原件').replace(/[\\/:*?"<>|]/g, '_')
    saveBlob(blob, `${name}.pdf`)
  }
  const exportVersionWord = async (versionNo) => {
    const context = { ...reportContext.current }
    try {
      setVersionExporting(versionNo)
      const blob = await downloadBsReport(taskId, 'docx', 'draft', { versionNo })
      if (reportIsCurrent(context)) saveBlob(blob, `${detail?.title || '核查报告'}-第${versionNo}版.docx`)
    } catch (e) { if (reportIsCurrent(context)) onError(e.message) } finally { if (reportIsCurrent(context)) setVersionExporting(null) }
  }
  // T8-U5A Task 2 人工复核闭环：签名与 U1 回调/契约一致——onReview(findingId, decision, note)，
  // 直接复用既有 reviewBsFinding。成功后只刷结论：当前 finding 仍存在则由 U2 保持选中；
  // 已消失（如重核查作废）则按 U2 规则回退第一条可见待办。失败时 submitReview 结束 submitting
  // → 右栏恢复可操作，错误经 onError 呈现为平实中文（不显示异常码）。
  const handleReview = async (findingId, decision, note) => {
    try {
      await reviewBsFinding(findingId, decision, note)
      await loadFindings()
      return true
    } catch (e) { onError(e.message); return false }
  }

  const { model, selectFinding, missCollapsed, toggleMiss, submitting, submitReview,
    reviewFilter, setReviewFilter,
  } = useReviewWorkbench({ detail, findings, onReview: handleReview })

  // 切换问题时回到新问题详情；原文分屏只属于当前选中的问题，不能残留在上一条问题上。
  const selectFindingAndClosePreview = useCallback((findingId) => {
    setStepFilter(null)
    setReviewOverviewMode(false)
    selectFinding(findingId)
    setPreviewRequest((current) => current ? ({ ...current, closed: true }) : current)
  }, [selectFinding])

  // 顶部上一条/下一条使用同一次状态更新切换问题与原文，避免先切页后仍渲染旧问题详情。
  const selectFindingAndOpenSource = useCallback((findingId, materialId, page) => {
    setStepFilter(null)
    setReviewOverviewMode(false)
    selectFinding(findingId)
    setPreviewRequest({ materialId, page: page || null, findingId, closed: false })
  }, [selectFinding])

  // 旧版覆盖提示项点击 → 回到左栏选中该条（先把筛选切到「全部」，避免该条被当前筛选藏住）。
  const selectBlockingFinding = useCallback((findingId) => {
    if (findingId == null) return
    setReviewFilter('all')
    setStepFilter(null)
    setReviewOverviewMode(false)
    selectFinding(findingId)
    setActiveView('review')
  }, [selectFinding, setReviewFilter, setActiveView])

  // ── V2：五步状态卡 + 待办分组（只做展示分组；判定仍取本轮既有 finding/冻结快照）──
  // 口径：步骤计数由后端冻结快照每步 kinds 给出（缺该字段的历史快照按运行组近似，
  // 派生层用 kindsSource 标出），此处不重算三态、不改严重度、不猜结论归属。
  const [stepFilter, setStepFilter] = useState(null)
  const [reviewOverviewMode, setReviewOverviewMode] = useState(false)
  // 待补材料的材料类筛选（与三桶筛选同属展示过滤，不改变结论语义）。
  const fiveStepView = useMemo(
    () => buildFiveSteps({ snapshot: referenceSnapshot, findings }),
    [referenceSnapshot, findings],
  )
  // 缺件面板的展示分区（可补件 / 非补件缺口）与计数全部来自后端 /todo。
  const missingPlan = useMemo(() => buildMissingPlan(todo), [todo])
// 刀F：五步总览（第 1 步缺件清单 + 各步主要问题）。只读派生：步骤归属复用冻结快照的 kinds，
// 缺件标签来自 finding DTO，可补类别来自 /todo；后续步骤不重复生成材料缺件。
  const stepOverview = useMemo(
    () => buildStepOverview({ snapshot: referenceSnapshot, findings, missingPlan }),
    [referenceSnapshot, findings, missingPlan],
  )
  const stepKinds = useMemo(() => {
    if (stepFilter == null) return null
    return fiveStepView.steps.find((step) => step.stepId === stepFilter)?.kinds ?? []
  }, [stepFilter, fiveStepView])
  const selectedStep = useMemo(
    () => stepOverview.steps.find((step) => step.stepId === stepFilter) || null,
    [stepFilter, stepOverview],
  )
  const scopedFindings = useMemo(
    () => filterFindings(findings.filter((finding) => finding?.open_term !== true), { kinds: stepKinds }),
    [findings, stepKinds],
  )
  const filtering = stepKinds != null
  // 左栏按筛选后的结论重排（组内计数与空组随筛选变化）；恢复「全部」时回到 U2 原模型。
  const scopedModel = useMemo(() => {
    if (!filtering) return model
    const groups = groupFindings({
      findings: scopedFindings,
      materialsById: buildMaterialsById(detail?.materials),
      subjectsById: buildSubjectsById(detail?.subjects),
      subjects: detail?.subjects || [],
    })
    return { ...model, groups }
  }, [model, scopedFindings, filtering, detail?.materials, detail?.subjects])
  // 筛选把当前选中项排除在外时，落到范围内第一条可见结论（只移动选择，不改结论语义）。
  useEffect(() => {
    if (!filtering) return
    if (model.selectedFindingId != null && scopedFindings.some((finding) => finding.id === model.selectedFindingId)) return
    const first = scopedFindings.find((finding) => finding.status !== 'miss') || scopedFindings[0]
    if (first) selectFinding(first.id)
  }, [filtering, scopedFindings, model.selectedFindingId, selectFinding])

  // 刀A：补件入口——带材料类（label/hint/可消条数/缺件标签）进入材料阶段。
  const supplementMaterial = useCallback((item) => {
    if (!item || !taskId) return
    const checklistSubjectId = item.synthetic_checklist ? item.subject_id : null
    const subjectIds = item.subjectMappingReliable === false || item.subject_mapping_reliable === false
      ? [] : (Array.isArray(item.subjectIds) ? item.subjectIds : (Array.isArray(item.subject_ids) ? item.subject_ids : (checklistSubjectId != null ? [checklistSubjectId] : [])))
    onOpenSupplementOverlay?.({
      materialLabel: item.label || item.role_label || '待补材料',
      stepLabel: '第 1 步 · 要素完整性核查',
      uploadCategory: supplementUploadCategoryOf(item),
      supplementMode: true,
      category: ({
        packet: 'submission_packet',
        general: 'general_contract',
        sub: 'sub_contract',
        labor: 'labor_contract',
      }[item.category || item.material_category]
        || item.category || item.material_category
        || (supplementUploadCategoryOf(item) === 'packet' ? 'submission_packet' : null)),
      itemId: item.itemId || item.item_id || item.checklist_item_id || item.finding_id || null,
      itemIds: Array.isArray(item.itemIds) ? item.itemIds : (Array.isArray(item.item_ids) ? item.item_ids : []),
      subjectItems: Array.isArray(item.subjectItems) ? item.subjectItems : (Array.isArray(item.subject_items) ? item.subject_items : []),
      subjectIds,
      subjectOptions: (subjectIds.length ? subjectIds : (detail?.subjects || [])
        .filter((subject) => subject.retired !== true && subject.subject_id != null)
        .map((subject) => subject.subject_id)).map((id) => {
        const subject = (detail?.subjects || []).find((row) => String(row.subject_id) === String(id))
        return { id, label: subject?.name || (subject?.stable_code ? subjectCodeLabel(subject.stable_code) : `主体 ${id}（名称待确认）`) }
      }),
      materialIds: Array.isArray(item.materialIds) ? item.materialIds : (Array.isArray(item.material_ids) ? item.material_ids : []),
      subjectId: (item.subjectMappingReliable === false || item.subject_mapping_reliable === false)
        ? null : (item.subjectId ?? (subjectIds.length === 1 ? subjectIds[0] : null)),
    })
  }, [taskId, onOpenSupplementOverlay, detail?.subjects])

  // T10-C C2：材料清单回看也走同一状态派生（单一表源），需要容器 ID 集合。
  const wbContainerIds = useMemo(
    () => collectContainerIds(detail?.materials, (detail?.zip_preparation?.containers || []).map((c) => c.container_material_id)),
    [detail],
  )

  const started = detail && detail.state !== 'setup'
  const checking = runningKey !== null || ['resetting', 'running', 'waiting'].includes(autoCheck.phase)
  const activity = userFacingActivity(currentActivity(events, detail?.state, autoCheck.phase, autoCheck.index, autoCheck.total, autoCheck.stage), findings)
  const done = detail?.state === 'done'
  // 刀B：历史版本与规则集更新提示的展示输入。只从后端 DTO 派生，不自行比对文件哈希。
  const versions = detail?.versions || []
  const declaredChangedIds = Array.isArray(ruleset?.declared_changed_item_ids)
    ? ruleset.declared_changed_item_ids.filter((id) => typeof id === 'string' && id.trim())
    : []
  const rulesetChanged = Boolean(ruleset) && (
    ruleset.changed === true
    || (ruleset.task_version && ruleset.current_version && ruleset.task_version !== ruleset.current_version)
  )
  const pendingUndecidable = findings.filter((finding) => finding.status === 'undecidable' && reviewBucket(finding) === 'pending').length
  const blockingUndecidable = findings.filter(blocksReportReview).length
  const pendingReviewCount = findings.filter(isUnreviewedFinding).length
  // 报告页的历史“完成复核”动作仍按原有命中问题口径展示；
  // 人工复核页的“生成审查报告”门禁单独只看未处置的无法核实项。
  const pendingProblems = findings.filter((finding) => finding.status === 'hit' && reviewBucket(finding) === 'pending').length
  const openReportFromReview = useCallback(() => {
    if (blockingUndecidable > 0) {
      const first = findings.find(blocksReportReview)
      setReviewFilter('pending')
      setStepFilter(null)
      if (first?.id != null) selectFindingAndClosePreview(first.id)
      onError(`还有 ${blockingUndecidable} 项无法核实待处理，处理后才能生成报告。`)
      return
    }
    setActiveView('report')
  }, [findings, onError, blockingUndecidable, selectFindingAndClosePreview, setActiveView, setReviewFilter])
  // 状态徽标使用用户任务语言，不暴露后端生命周期术语。
  const stateBadge = checking
    ? { label: '核查进行中', cls: 'border-[#C8E6C9] bg-[#E8F5E9] text-[#2E7D32]' }
    : done
    ? { label: '报告已生成', cls: 'border-[#8B7355] bg-[#F7F1E9] text-[#5B4A2F]' }
    : detail?.state === 'running' && referenceSnapshot?.steps?.length > 0
      && referenceSnapshot.steps.every((step) => step?.state === 'completed' || step?.state === 'out_of_scope')
    ? { label: '结果已生成，待收口', cls: 'border-[#F0DCB8] bg-[#FFF8E9] text-[#96651C]' }
    : started
      ? { label: '核查进行中', cls: 'border-[#C8E6C9] bg-[#E8F5E9] text-[#2E7D32]' }
      : { label: '材料准备中', cls: 'border-[#E5E5E5] bg-[#F7F1E9] text-[#808080]' }

  const header = buildTaskHeader({ detail, report })
  // 材料清单包含主体级应报项与实际文件，导航徽标必须复用材料行的状态口径，
  // 不能只读 /todo 的聚合类别，否则会出现导航与清单缺件数漂移。
  // 状态真实性:后端 state=running 不等于「后台还在跑」——自动核查编排在浏览器里,
  // 关页即停。只有本页确有编排在跑(或本次会话标记 running 即将恢复)才算运行中。
  const orchestrationActive = ['running', 'waiting'].includes(autoCheck.phase) || readAutoRunStatus(taskId, reviewRunId) === 'running'
  const backendRunningWithoutOrchestration =
    detail?.state === 'running' && !orchestrationActive && !findingsLoading
  const flowStage = activeView === 'report' ? 'report' : activeView === 'run' ? 'run' : 'review'

  return (
    <PrototypeV3Shell
      taskState={detail?.state}
      stage={flowStage}
      reachedIndex={reachedIndex}
      onStageSelect={(stage) => {
        if (publicationPending && ['review', 'report'].includes(stage)) return
        if (stage === 'upload') onStageSelect?.(stage)
        else setActiveView(stage)
      }}
    >
      <div data-testid="workbench-stage" className="flex h-full min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 w-full flex-1 flex-col overflow-y-auto px-6 pb-6 pt-5 [scrollbar-gutter:stable]">
        {activeView === 'run' && detail?.supplement_plan?.status === 'running' && <PreviousSupplementReview key={`${taskId}-${detail?.supplement_plan?.superseded_version_no}`} taskId={taskId} versionNo={detail?.supplement_plan?.superseded_version_no} />}
        <header data-testid="workbench-header" className={activeView === 'run' ? 'sr-only' : 'sticky top-0 z-20 shrink-0 mb-[14px] bg-transparent'}>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h1 data-testid="workbench-title" aria-label={header.headline || detail?.title || '分包合规核查'} className="truncate text-[23px] font-bold tracking-[.01em] text-[#17283D]">
              {header.headline}
              {header.company && <span data-testid="workbench-header-company" aria-hidden="true" className="font-bold"> · {header.company}</span>}
            </h1>
            {/* 抬头第二行按原型（类型 · 金额 · 项目 · 签订 · 页数整册）排列；
                取不到的字段整段不渲染，不渲染「—」占位（lib/taskHeaderModel.js 有说明）。 */}
            {header.fields.length > 0 && (
              <div data-testid="workbench-header-fields" className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[#6B7280]">
                {header.fields.map((field, index) => (
                  <span key={field.key} className="inline-flex items-center gap-2">
                    {index > 0 && <i className="not-italic text-[#D6CDBF]">/</i>}
                    <span>{field.label === '类型' || field.label === '项目' ? field.value : `${field.label} ${field.value}`}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
          {activeView !== 'review' && activeView !== 'report' && <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {started && detail?.state !== 'preparing' && (
              <button
                type="button"
                data-testid="edit-materials"
                disabled={checking}
                onClick={() => onOpenSupplementOverlay?.({
                  materialLabel: missingPlan?.missing?.[0]?.label || '待补材料',
                  stepLabel: '第 1 步 · 要素完整性核查',
                })}
                className="min-h-9 rounded-[6px] border border-[#B48C55] bg-[#FFFAF2] px-3 py-1.5 text-xs text-[#8A5E1C] hover:bg-[#FFF8E9] disabled:opacity-50"
              >
                上传 / 补件
              </button>
            )}
            {started && detail?.state !== 'preparing' && (
              <button
                type="button"
                data-testid="rerun-check"
                disabled={runningKey !== null}
                onClick={() => rerunTask()}
                className="min-h-9 rounded-[4px] border border-[#E5E5E5] px-3 py-1.5 text-xs text-[#6B7280] hover:bg-[#FAFAF7] disabled:opacity-50"
              >
                重新核查
              </button>
            )}
              <button
                type="button"
                disabled={resetting}
                onClick={onReset}
                className="min-h-9 rounded-[4px] border border-[#E5E5E5] px-3 py-1.5 text-xs text-[#6B7280] hover:bg-[#FAFAF7] disabled:opacity-50"
              >
                重新开始
              </button>
              <button
                type="button"
                onClick={onOpenReviewNotes}
                className="min-h-9 rounded-[4px] border border-[#DCE5EE] px-3 py-1.5 text-xs text-[#587089] hover:bg-[#F4F7FA]"
              >
                评审说明
              </button>
            <span className={`sr-only rounded-full border px-2 py-1 text-xs ${stateBadge.cls}`}>{stateBadge.label}</span>
          </div>}
          {activeView === 'review' && (
            <div data-testid="review-header-actions" className="flex shrink-0 flex-wrap items-center justify-end gap-2">
              <span className={`rounded-full border px-2.5 py-1 text-xs ${pendingUndecidable > 0 ? 'border-[#EAD5AA] bg-[#FFF8E9] text-[#96651C]' : 'border-[#C8E4CF] bg-[#EFFAF2] text-[#2F7D49]'}`}>
                {pendingReviewCount > 0 ? `待复核 ${pendingReviewCount} 项` : '核查完成'}
              </span>
            </div>
          )}
          {activeView === 'review' && (
            <div className="sr-only" aria-label="人工复核高级操作">
              <button type="button" data-testid="edit-materials" onClick={() => onOpenSupplementOverlay?.({ materialLabel: missingPlan?.missing?.[0]?.label || '待补材料', stepLabel: '第 1 步 · 要素完整性核查' })}>上传 / 补件</button>
              <button type="button" data-testid="rerun-check" disabled={runningKey !== null} onClick={() => rerunTask()}>重新核查</button>
              <button type="button" onClick={onReset}>重新开始</button>
              <button type="button" onClick={onOpenReviewNotes}>评审说明</button>
            </div>
          )}
        </div>
        </header>
      {/* 工作区入口与原型 tabs 对齐；运行页是异步过程，不在四个工作区中占一个标签。 */}
      {(versions.length > 0 || rulesetChanged) && (
        <>
          {/* v3 原型的 Overview 首屏不展示历史版本折叠条；历史版本仍保留在 DOM 和报告上下文中，
              仅作为次级只读能力，不挤占总览主路径。 */}
          <details data-testid="review-version-history" className="hidden mt-3 shrink-0 rounded-[6px] border border-[#E5E5E5] bg-white px-4 py-2.5">
          <summary className="cursor-pointer select-none text-[13px] text-[#1A1A1A]">
            历史审查版本 <span className="text-[11px] text-[#9CA3AF]">历史版本（只读，未提供冻结接口），不影响当前核查</span>
          </summary>
          {/* 刀B（BS-P2-04）：规则集更新提示。数字与清单全部来自后端 /ruleset；
              读不到时只说明「暂不可用」，不伪造无变化或 0 条。 */}
          {rulesetLoading && !ruleset && (
            <p data-testid="ruleset-loading" className="mt-2 text-[11px] text-[#9CA3AF]">正在读取规则集信息…</p>
          )}
          {rulesetError && (
            <p data-testid="ruleset-unavailable" className="mt-2 rounded-[4px] border border-[#F0DCB8] bg-[#FFFDF8] px-2 py-1.5 text-[11px] leading-relaxed text-[#92400E]">
              规则集信息暂不可用（{rulesetError}），本次不显示规则变化数量。
            </p>
          )}
          {rulesetChanged && (
            <div data-testid="ruleset-update-notice" className="mt-2 rounded-[4px] border border-[#F0DCB8] bg-[#FFFDF8] px-3 py-2 text-[12px] leading-relaxed text-[#92400E]">
              <p data-testid="ruleset-version-line">
                规则集：{ruleset.task_version || '未记录'} → 当前 {ruleset.current_version || '未记录'}
                {declaredChangedIds.length > 0
                  ? `（${declaredChangedIds.length} 条规则变化）`
                  : '（变化清单未登记）'}
              </p>
              {declaredChangedIds.length > 0 ? (
                <p data-testid="ruleset-changed-items" className="mt-0.5 break-words text-[11px] text-[#6B7280]">
                  变化项：{declaredChangedIds.slice(0, 20).join('、')}
                  {declaredChangedIds.length > 20 ? ` 等 ${declaredChangedIds.length} 条` : ''}
                </p>
              ) : (
                <p data-testid="ruleset-changed-empty" className="mt-0.5 text-[11px] text-[#6B7280]">
                  本次规则变化清单未登记，只能全量重跑。
                </p>
              )}
              <button
                type="button"
                data-testid="ruleset-rerun"
                disabled={runningKey !== null}
                onClick={() => rerunTask(declaredChangedIds.length
                  ? { scope: 'items', itemIds: declaredChangedIds, statusKey: 'ruleset' }
                  : { statusKey: 'ruleset' })}
                className="mt-2 min-h-8 rounded-[4px] border border-[#8B7355] bg-white px-2.5 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {runningKey !== null
                  ? '重跑中…'
                  : declaredChangedIds.length > 0
                    ? `按新规则重跑（${declaredChangedIds.length} 条）`
                    : '按新规则全量重跑'}
              </button>
              {rulesetRerunStatus && (
                <p data-testid="ruleset-rerun-status" className="mt-1 text-[11px] text-[#2E7D32]">{rulesetRerunStatus}</p>
              )}
              {rulesetRerunDetail && (
                <p data-testid="ruleset-rerun-detail" className="mt-1 text-[11px] text-[#6B7280]">{rulesetRerunDetail}</p>
              )}
              {rulesetRerunError && (
                <p data-testid="ruleset-rerun-error" className="mt-1 text-[11px] text-[#C62828]">{rulesetRerunError}</p>
              )}
            </div>
          )}
          {versions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5 border-t border-[#F3EDE3] pt-2">
              {versions.map((version) => (
                <div key={version.version_no} className="flex flex-col items-start gap-0.5">
                  <button
                    type="button"
                    data-testid={`review-version-${version.version_no}`}
                    onClick={() => loadVersion(version.version_no)}
                    disabled={versionLoading}
                    className={`rounded-[4px] border px-2 py-1 text-[11px] ${versionDetail?.version_no === version.version_no ? 'border-[#8B7355] bg-[#F7F1E9] text-[#5B4A2F]' : 'border-[#E5E5E5] text-[#6B7280] hover:bg-[#FAFAF7]'}`}
                  >
                    第 {version.version_no} 版 · {REVIEW_VERSION_TRIGGER_LABELS[version.trigger] || '历史快照'}
                  </button>
                  <span data-testid={`review-version-ruleset-${version.version_no}`} className="pl-1 text-[10px] text-[#9CA3AF]">
                    规则集：{version.ruleset_version || '未记录'}
                  </span>
                </div>
              ))}
            </div>
          )}
          {versions.length > 0 && <ReviewVersionDiff taskId={taskId} versions={versions} />}
          {versionDetail && (
            <div data-testid="review-version-detail" className="mt-2 max-h-[45vh] space-y-2 overflow-y-auto rounded-[4px] bg-[#FAFAF7] px-3 py-2 text-[12px] leading-relaxed text-[#6B7280]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-[#5B4A2F]">第 {versionDetail.version_no} 版问题摘要 · 只读</span>
                <button type="button" disabled={versionExporting != null || versionLoading} onClick={() => exportVersionWord(versionDetail.version_no)} className="min-h-8 rounded border border-[#DED2C0] bg-white px-2 text-[#8B7355] disabled:opacity-60">{versionExporting === versionDetail.version_no ? '导出中…' : '导出本版 Word'}</button>
              </div>
              <ul className="mt-1 space-y-1">
                {(versionDetail.snapshot?.findings || []).map((finding) => (
                  <li key={`${versionDetail.version_no}-${finding.finding_id || finding.item_id}`}>
                    <span className="mr-1 rounded bg-[#F7F1E9] px-1 text-[11px]">{statusLabelOf(finding)}</span>
                    {finding.message}
                  </li>
                ))}
                {!(versionDetail.snapshot?.findings || []).length && <li>该版本没有保存逐项结论。</li>}
              </ul>
              <ReferenceSnapshot key={versionDetail.version_no} snapshot={versionDetail.snapshot?.reference_snapshot ?? null} historical onDownload={(reference) => downloadReference(reference, versionDetail.version_no)} />
              <AiAdvisorySummary items={versionDetail.snapshot?.ai_advisories} coverage={versionDetail.snapshot?.report?.ai_coverage} findings={versionDetail.snapshot?.findings} />
            </div>
          )}
          </details>
        </>
      )}

      {activeView === 'materials' ? (
        <div className="min-h-0 flex-1 overflow-y-auto pt-4">
          <ProjectBasicInformation report={report} detail={detail} onOpenSource={openSource} onConfirmEmployer={confirmEmployer} confirmingEmployer={confirmingEmployer} />
          <PrototypeMaterialsWorkspace
            detail={detail}
              checklistSummary={todo?.checklist_summary}
              missingPlan={missingPlan}
              findings={findings}
              stepOverview={stepOverview}
            onOpen={(materialId) => openSource(materialId, null)}
            onSupplement={(material) => onOpenSupplementOverlay?.({
              materialLabel: material?.role_label || material?.file_name || '材料补件',
              stepLabel: '第 1 步 · 要素完整性核查',
              uploadCategory: supplementUploadCategoryOf(material),
              supplementMode: true,
              category: ({
                packet: 'submission_packet',
                general: 'general_contract',
                sub: 'sub_contract',
                labor: 'labor_contract',
              }[material?.material_category || material?.category]
                || material?.material_category || material?.category
                || (supplementUploadCategoryOf(material) === 'packet' ? 'submission_packet' : null)),
              itemId: material?.item_id || material?.checklist_item_id || null,
              itemIds: Array.isArray(material?.item_ids)
                ? material.item_ids
                : (material?.item_id != null
                  ? [material.item_id]
                  : (material?.checklist_item_id != null ? [material.checklist_item_id] : [])),
              subjectIds: Array.isArray(material?.subject_ids)
                ? material.subject_ids
                : (material?.subject_id != null ? [material.subject_id] : []),
              subjectOptions: (detail?.subjects || [])
                .filter((subject) => String(subject.subject_id) === String(material?.subject_id ?? material?.subjectId ?? ''))
                .map((subject) => ({ id: subject.subject_id, label: subject.name || subjectCodeLabel(subject.stable_code) })),
              materialIds: Array.isArray(material?.material_ids)
                ? material.material_ids
                : (material?.material_id != null ? [material.material_id] : []),
              subjectId: material?.subject_id ?? material?.subjectId ?? null,
              packetId: material?.packet_id ?? material?.packetId ?? null,
            })}
            onRequestEdit={onRequestEditMaterials}
            onReplace={(material) => onOpenSupplementOverlay?.({
              materialLabel: material?.role_label || material?.file_name || '材料替换',
              stepLabel: '材料清单 · 当前文件替换',
              uploadCategory: material?.role || null,
              supplementMode: true,
              category: material?.material_category || material?.category || 'unknown',
              itemId: material?.item_id || null,
              replacesMaterialId: material?.id ?? material?.material_id ?? null,
              subjectId: material?.subject_id ?? material?.subjectId ?? null,
              packetId: material?.packet_id ?? material?.packetId ?? null,
            })}
            onOpenReview={() => setActiveView('review')}
            onBack={() => setActiveView(materialsOrigin)}
          />
        </div>
      ) : activeView === 'report' ? (
        <div className="min-h-0 flex-1 pt-4">
          <ReportView
            report={reports[reportStage]}
            stage={reportStage}
            onStageChange={changeReportStage}
            prototypeMode
            stageBusy={reportLoading[reportStage]}
            blockingByStage={reportGates}
            onSelectBlocking={selectBlockingFinding}
            onBack={() => setActiveView('review')}
            onExport={exportReport}
            exporting={exporting}
            onRefresh={refreshReport}
            refreshing={reportLoading[reportStage]}
            refreshMessage={reportRefreshMessage}
            onDownloadReference={downloadReference}
            onOpenSource={openSource}
            onShowBasicInformation={() => {
              setActiveView('materials')
              requestAnimationFrame(() => document.getElementById('project-basic-information')?.focus())
            }}
            onComplete={!done ? completeTask : undefined}
            completing={completing}
            completeDisabled={pendingProblems > 0 || checking}
            pendingProblems={pendingProblems}
            completed={done}
            started={started}
            error={reportStageErrors[reportStage]}
            refreshDisabled={runningKey !== null}
            opinion={opinion}
            opinionMarkdown={opinionMarkdown}
            opinionLoading={opinionLoading}
            opinionError={opinionError}
            onGenerateOpinion={generateOpinion}
            onExportOpinion={exportOpinion}
            opinionExporting={opinionExporting}
          />
        </div>
      ) : activeView === 'run' ? (
        <CheckRunView
          taskState={detail?.state}
          snapshot={referenceSnapshot}
          findings={findings}
          autoCheck={reportGates.draft?.code === 'review_inputs_changed' && runningKey == null
            ? { ...autoCheck, phase: 'failed', stage: 'ai', error: reportGates.draft.message, recheckRequired: true }
            : autoCheck}
          activity={activity}
          progress={detail?.progress || null}
            events={events}
            materials={detail?.materials || []}
            zipPreparation={detail?.zip_preparation || null}
            supplementPlan={detail?.supplement_plan || null}
          publicationPending={publicationPending}
          onRetry={retryAutoChecks}
          onRecheck={() => rerunTask()}
          onEnterReview={() => setActiveView('review')}
          onReviewReady={markReviewReady}
        />
      ) : (
      <>
      <div data-testid="workbench-content" className={`flex min-h-0 flex-1 flex-col pr-1 ${activeView === 'overview' ? 'pt-[18px]' : ''} ${activeView === 'review' ? 'overflow-y-auto xl:overflow-hidden' : 'overflow-y-auto'}`}>
        <div className={activeView === 'review' ? 'flex min-h-0 flex-1 flex-col gap-3' : 'space-y-4'}>
          {activeView === 'overview' && (previewRequest ? (
            <MaterialPreviewModal
              materials={detail?.materials || []}
              containerIds={wbContainerIds}
              activeMaterialId={previewRequest.materialId}
              page={previewRequest.page}
              fullPage
              onSelect={(materialId) => setPreviewRequest((current) => ({ ...current, materialId, page: null, closed: false }))}
              onClose={() => setPreviewRequest(null)}
              onError={onError}
            />
          ) : (
            <>
              <PrototypeOverviewWorkspace
                findings={findings}
                stepOverview={stepOverview}
                missingPlan={missingPlan}
                checklistSummary={todo?.checklist_summary}
                materialIssues={report?.material_issues || []}
                loading={findingsLoading || autoCheck.phase === 'running' || (todoLoading && missingPlan == null)}
                error={findingsError || null}
                onOpenReport={() => setActiveView('report')}
                onRerun={started && detail?.state !== 'preparing' ? () => rerunTask() : undefined}
                rerunDisabled={runningKey !== null}
                onSelectStep={(stepId) => {
                  setStepFilter(stepId)
                  setReviewOverviewMode(false)
                  setActiveView('review')
                }}
                onSelectFinding={(findingId) => {
                  selectFindingAndClosePreview(findingId)
                  setActiveView('review')
                }}
                onSupplement={supplementMaterial}
              />
            </>
          ))}
          {/* 主操作查看完整AI初稿；人工复核完成另行确认。 */}
          {activeView === 'review' && <div className="sticky top-0 z-10 flex flex-wrap items-center justify-end gap-2 bg-[#fafafa] py-1">
            {['running', 'waiting'].includes(autoCheck.phase) && (
              <div data-testid="auto-check-progress" role="status" className="inline-flex items-center gap-2 rounded-[6px] border border-[#DCE8DD] bg-[#F3FAF3] px-3 py-2 text-[13px] text-[#2E7D32]">
                <span>{autoCheck.phase === 'waiting' ? '正在等待已启动的 AI 核查' : autoCheck.stage === 'ai' ? '正在生成 AI 辅助建议' : '系统正在自动核查'}</span>
                <span className="text-[12px] text-[#6B7280]">
                  {Math.min(autoCheck.index + 1, autoCheck.total)} / {autoCheck.total}
                </span>
              </div>
            )}
            {autoCheck.phase === 'failed' && (
              <div data-testid="auto-check-error" className="inline-flex max-w-full flex-wrap items-center gap-2 rounded-[6px] border border-[#F3C9C9] bg-[#FDF3F3] px-3 py-2 text-[13px] text-[#C62828]">
                <span>自动核查未完成：{autoCheck.error}</span>
                <button type="button" onClick={retryAutoChecks} className="rounded border border-[#C62828] px-2 py-1 text-[12px] hover:bg-white">重试核查</button>
              </div>
            )}
            {autoCheck.phase === 'done' && !done && (
              <span data-testid="auto-check-done" className="text-[13px] text-[#2E7D32]">{autoCheck.warning || '核查已完成，请核对并处理问题'}</span>
            )}
            {/* 编排未运行但服务端仍有未完成分组：给一个可理解的出口，
                避免「进度条停住 + 无任何按钮」的死状态（任务 74 曾卡 5 小时）。 */}
            {!checking && autoCheck.phase !== 'failed' && detail?.state === 'running' && pendingAutoSteps.length > 0 && (
              <div data-testid="auto-check-resume" className="inline-flex max-w-full flex-wrap items-center gap-2 rounded-[6px] border border-[#F0DCB8] bg-[#FDFAF4] px-3 py-2 text-[13px] text-[#92400E]">
                <span>还有 {pendingAutoSteps.length} 组核查未跑完</span>
                <button
                  type="button"
                  data-testid="auto-check-resume-button"
                  onClick={retryAutoChecks}
                  className="rounded border border-[#92400E] px-2 py-1 text-[12px] hover:bg-white"
                >
                  继续核查
                </button>
              </div>
            )}
          </div>}

          {/* 材料问题（诚实降级，平实文案来自后端） */}
          {/* ★ workbench 主体（T8-U5A Task 1/2/3/5，T8-U5-R1A Task 1 前移）：
              三栏是核查主路径首位，必须先于报告等长内容出现。
              DOM 顺序直接按 JSX 结构排定（不使用 CSS order 制造视觉/结构不一致）。
              view-model（groups/selectedFinding/evidence/actions）与选择态全部来自 U2，入口不重复派生；
              左栏选中 → 中栏详情与右栏证据/处置同步。旧 EvidencePanel 长列表已下线，不再作主 finding 入口。 */}
          {activeView === 'review' && <ReviewWorkbenchLayout
            className="min-h-0 xl:min-h-0 xl:flex-1 xl:h-full"
            prototypeMode
            model={scopedModel}
            loading={findingsLoading || autoCheck.phase === 'running'}
            error={findingsError || (autoCheck.phase === 'failed' ? autoCheck.error : null)}
            readingCoverage={detail?.reading_coverage || null}
            missCollapsed={missCollapsed}
            onToggleMiss={toggleMiss}
            onSelect={selectFindingAndClosePreview}
            onSelectAndOpenSource={selectFindingAndOpenSource}
            onReview={submitReview}
            onReturnOverview={() => { setStepFilter(null); setReviewOverviewMode(true) }}
            submitting={submitting}
            onError={onError}
            onComplete={openReportFromReview}
            completeLabel="生成审查报告"
            completeDisabled={blockingUndecidable > 0 || checking}
            previewMaterialId={previewRequest?.materialId ?? null}
            previewPage={previewRequest?.page ?? null}
            previewFindingId={previewRequest?.findingId ?? null}
            previewClosed={previewRequest?.closed === true}
            previewContainerIds={wbContainerIds}
            onOpenSource={openSource}
            onSupplement={supplementMaterial}
            onPreviewMaterialChange={(materialId) => setPreviewRequest((current) => ({ ...(current || {}), materialId: Number(materialId), page: null, closed: false }))}
            onClosePreview={() => setPreviewRequest((current) => current ? ({ ...current, closed: true }) : null)}
            reviewFilter={reviewFilter}
            onFilterChange={(nextFilter) => {
              if (nextFilter !== reviewFilter) {
                setPreviewRequest((current) => current ? ({ ...current, closed: true }) : current)
              }
              setReviewFilter(nextFilter)
            }}
            emptyLabel={filtering
              ? '当前筛选下没有结论。点「全部步骤」或再点一次已选项，即可看回全部结论。'
              : null}
            todoPanel={<ReviewPrototypeLeftRail
              steps={stepOverview.steps}
              selectedStepId={stepFilter}
              onSelectStep={(stepId) => {
                setStepFilter((current) => (current === stepId ? null : stepId))
                setReviewOverviewMode(stepId == null || stepFilter === stepId)
                setPreviewRequest((current) => current ? ({ ...current, closed: true }) : current)
              }}
              stepOverview={stepOverview}
              checklistSummary={todo?.checklist_summary}
              detail={detail}
              missingPlan={missingPlan}
              loading={findingsLoading || autoCheck.phase === 'running' || (todoLoading && missingPlan == null)}
              onSupplement={supplementMaterial}
            />}
            overviewPanel={<StepOverviewPanel
              model={stepOverview}
              loading={findingsLoading || autoCheck.phase === 'running' || (todoLoading && missingPlan == null)}
              error={findingsError || null}
              onSelectStep={(stepId) => {
                setStepFilter(stepId)
                setReviewOverviewMode(false)
              }}
              onSelectFinding={selectFindingAndClosePreview}
              onSupplement={supplementMaterial}
            />}
            overviewMode={reviewOverviewMode}
            stepPanel={selectedStep ? <ReviewStepDetailPanel
              step={selectedStep}
              detail={detail}
              checklistSummary={todo?.checklist_summary}
              missingPlan={missingPlan}
              findings={findings}
              onSelectFinding={selectFindingAndClosePreview}
              onSupplement={supplementMaterial}
              onConfirmMaterial={materialConfirming || findingsError ? undefined : confirmMaterial}
            /> : null}
          />}

          {/* 合同要素（T8-U5-R1A Task 1）：次级折叠，位于三栏之后，不阻挡用户首先进入三栏核查。
              展开行为与内容渲染逻辑保持原样，仅外包一层 <details>。 */}
          {activeView === 'overview' && report && report.contract_facts && report.contract_facts.length > 0 && (
            <details className="hidden rounded-[6px] border border-[#E5E5E5] bg-white">
              <summary className="cursor-pointer select-none px-4 py-2.5 text-[13px] text-[#1A1A1A]">
                合同要素 <b className="tabular-nums">{report.contract_facts.length}</b> 份
                <span className="ml-2 text-[12px] text-[#9CA3AF]">
                  从合同文本抽出的标准信息（金额/比例带单位；抽不到的如实标注缺失）
                </span>
              </summary>
              <ul className="divide-y divide-[#F3EDE3] border-t border-[#EFE6D8]">
                {report.contract_facts.map((cf, i) => (
                  <li key={i} className="px-4 py-2.5">
                    <p className="text-[12px] font-medium text-[#1A1A1A]">
                      {cf.file_name}
                      <span className="ml-2 rounded bg-[#F7F1E9] px-1.5 py-0.5 text-[11px] text-[#8B7355]">
                        {cf.role_label}
                      </span>
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {CONTRACT_FACT_ORDER.map((key) => {
                        const f = (cf.fields || {})[key] || {}
                        const label = CONTRACT_FACT_LABELS[key] || key
                        if (f.status === 'hit') {
                          return (
                            <li key={key} className="text-[12px] leading-relaxed text-[#6B7280]">
                              <b className="text-[#1A1A1A]">{label}</b>：{contractFactValueText(f)}
                            </li>
                          )
                        }
                        const miss = (f.missing || []).join('、') || '该要素'
                        return (
                          <li key={key} className="text-[12px] leading-relaxed text-[#9CA3AF]">
                            <b className="text-[#6B7280]">{label}</b>：未认定（缺：{miss}）
                          </li>
                        )
                      })}
                    </ul>
                  </li>
                ))}
              </ul>
            </details>
          )}

        </div>
      </div>
          {/* 核查动态时间线（SSE 实时推送：登记/解析/核查/导出全程动态，含准备阶段回放）。
              三栏接入不改变 SSE 能力：事件照常累积，收在此次级折叠区呈现（T8-U5A Task 6）。 */}
          {(events.length > 0 || checking || backendRunningWithoutOrchestration) && (
            <details data-testid="check-timeline" className="hidden mt-3 shrink-0 overflow-hidden rounded-[6px] border border-[#E5E5E5] bg-white px-4 py-2.5">
              <summary className="cursor-pointer select-none text-[13px] text-[#1A1A1A]">
                <span>核查动态</span>{backendRunningWithoutOrchestration && <span className="ml-1 text-[#92400E]">· 需要继续核查</span>}
              </summary>
              <div data-testid="timeline-scroll" className="max-h-[160px] overflow-y-auto overscroll-contain">
              {backendRunningWithoutOrchestration && (
                <div
                  role="status"
                  data-testid="bs-run-truth-notice"
                  className="mt-2 rounded-[6px] border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
                >
                  任务在服务端仍标记为「运行中」，但本页没有正在执行的自动核查（关闭页面后编排不会继续）。
                  <button type="button" onClick={() => rerunTask()} className="ml-1 underline underline-offset-2">继续核查</button>
                </div>
              )}
              {checking && <p role="status" className="mt-2 flex items-center gap-2 text-xs text-[#2E7D32]"><span className="bs-progress-dot h-1.5 w-1.5 rounded-full bg-[#2E7D32]" />{activity}</p>}
              <ul className="mt-2 space-y-1.5 border-t border-[#F3EDE3] pt-2">
                {[...events].reverse().map((ev) => (
                  <li key={ev.sequence} className="flex items-start gap-2 text-[12px] leading-relaxed">
                    <span className="shrink-0 tabular-nums text-[#9CA3AF]">{timelineTime(ev.created_at)}</span>
                    <span className="shrink-0 rounded bg-[#F7F1E9] px-1.5 py-0.5 text-[#8B7355]">{ev.label}</span>
                    {ev.message && <span className="min-w-0 text-[#6B7280]">{ev.message}</span>}
                  </li>
                ))}
              </ul>
              </div>
            </details>
          )}

      </>
      )}
      </div>
      </div>
    </PrototypeV3Shell>
  )
}
