const ACTIVE_PROGRESS_PHASES = new Set(['material_reading', 'intelligent_check', 'ai_checking'])
const ACTIVE_LOCAL_PHASES = new Set(['resetting', 'running', 'waiting'])

/**
 * Server-authoritative write gate for task materials and reruns.
 * A running task is writable again only after every deterministic step is
 * complete and no material/AI phase is still active.
 */
export function taskMutationBusy({
  taskState,
  steps = [],
  progressPhase = null,
  localPhase = null,
  running = false,
  allowStaleRunning = false,
} = {}) {
  if (running || ACTIVE_LOCAL_PHASES.has(localPhase) || taskState === 'preparing') return true
  if (taskState !== 'running') return false
  // 浏览器编排已停止且后端仍停留在 running 时，允许用户通过 rerun
  // 让服务端重新建立一代核查；真实材料读取/智能核查阶段仍由上面的
  // progressPhase 和 localPhase 保护，避免并发破坏正在运行的任务。
  if (allowStaleRunning) return false
  if (ACTIVE_PROGRESS_PHASES.has(progressPhase)) return true
  if (!Array.isArray(steps) || steps.length === 0) return true
  return steps.some((step) => !['completed', 'out_of_scope'].includes(step?.state))
}
