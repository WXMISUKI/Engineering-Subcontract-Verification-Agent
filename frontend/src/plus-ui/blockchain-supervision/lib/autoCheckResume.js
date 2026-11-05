// 自动核查编排的「该不该跑、跑哪些组」判据。
//
// 背景（2026-10-27）：恢复编排此前只看 `bs:auto-check:<taskId>`，而它存在 sessionStorage，
// 按标签页会话隔离 —— 重新登录 / 换标签页 / 会话失效后即丢失。结果是服务端 `bs_task.state`
// 仍为 `running`、分组并未跑完，页面却停在 25% 且因为 `autoCheck.phase === 'idle'`
// 连「重试核查」按钮都不渲染 ⇒ 用户没有任何出口（任务 74 曾卡 5 小时）。
//
// 2026-10-29（任务 77 复现）：旧判据把「要不要跑」拆成两个互斥分支 ——
// `shouldStart`（本地标记非 completed/failed）与 `shouldResume`（要求 !autoRunOnMount）。
// 而本地标记只按 taskId 存、没有「代」的概念：材料被修改后重新开始核查，标记仍是上一代的
// `completed`，`shouldStart` 恒假；刷新后 `autoRunOnMount` 仍为真，`shouldResume` 也走不到
// ⇒ 上一代结论被作废、新一代一次 /run 都没发出，页面却显示「核查进行中」。
//
// 修法两层：
// 1. 本地标记绑定当前代（由调用方用服务端 `reference_snapshot.review_run_id` 校验，
//    换代即失效），不再依赖任何入口「记得清理」；
// 2. 启动与恢复收敛为同一个纯函数 `planAutoChecks`，一处判据、可单测。

// 本地编排状态**绑定当前代**：代的身份来自服务端 `reference_snapshot.review_run_id`
// （每次 start 重写，换代即不同）。因此材料修改后重新开始核查时，上一代的
// `completed` 自动失效，不依赖任何入口「记得清理」（2026-10-29 任务 77 复现过的死锁）。
// 例外只留一条：`running` 是「本标签页正在编排」的瞬时事实、不是某一代的结论，
// 允许跨代命中——它最坏只多触发一次续跑，而续跑循环本身会跳过已完成的组。
function autoRunStorageKey(taskId) {
  return `bs:auto-check:${taskId}`
}

export function readAutoRunStatus(taskId, runId) {
  try {
    const raw = window.sessionStorage.getItem(autoRunStorageKey(taskId))
    if (!raw) return ''
    const cached = JSON.parse(raw)
    if (!cached || typeof cached !== 'object') return ''
    if (cached.status === 'running') return 'running'
    return cached.runId === (runId || null) ? (cached.status || '') : ''
  } catch { return '' }
}

export function writeAutoRunStatus(taskId, runId, status) {
  try {
    window.sessionStorage.setItem(autoRunStorageKey(taskId), JSON.stringify({ runId: runId || null, status }))
  } catch { /* storage disabled */ }
}

export function clearAutoRunStatus(taskId) {
  try { window.sessionStorage.removeItem(autoRunStorageKey(taskId)) } catch { /* storage disabled */ }
}

/** 从 `reference_snapshot.runs` 取出已完成的核查组 id 集合。 */
export function completedAutoCheckGroups(runs) {
  return new Set(
    (Array.isArray(runs) ? runs : [])
      .filter((run) => run && run.state === 'completed' && run.group_id)
      .map((run) => run.group_id),
  )
}

/**
 * 当前仍需执行的核查步骤（key 列表，顺序同 `AUTO_CHECK_ORDER`）。
 * - `taskState === 'done'` 或空 ⇒ 一律返回空数组：已完成的任务不在后台静默重跑。
 * - `runs` 缺项时按「未完成」处理，保证服务端无快照的旧任务仍能被恢复。
 */
export function pendingAutoCheckSteps({ taskState, steps = [], runs = [], groupOf } = {}) {
  if (!taskState || taskState === 'done') return []
  const completed = completedAutoCheckGroups(runs)
  return steps.filter((key) => !completed.has(groupOf ? groupOf(key) : key))
}

/**
 * 本轮该做什么：`{ action: 'start' | 'resume' | 'none', pending }`。
 *
 * - `start`：刚点完「开始核查」进入工作台，本代还没有任何已完成的组；
 * - `resume`：本代已经跑过一部分（或本次会话明确标记 running），继续跑剩下的；
 * - `none`：不用动（任务已收口 / 尚未开始 / 本代已跑完 / 本代明确失败 / 纯只读进入）。
 *
 * 入参里 `localStatus` 必须已经过「代」的校验（见 Workbench 的 readAutoRunStatus），
 * 这里不再关心它来自本地还是服务端，只按状态语义决策 —— 判据只定义这一次。
 */
export function planAutoChecks({
  taskState,
  steps = [],
  runs = [],
  localStatus = '',
  autoRun = false,
  hasFindings = false,
  groupOf,
} = {}) {
  // 未启动（setup/preparing）不给后台静默开跑；已收口也不再重跑。
  if (taskState !== 'running' && taskState !== 'review') return { action: 'none', pending: [] }
  const pending = pendingAutoCheckSteps({ taskState, steps, runs, groupOf })
  if (!pending.length) return { action: 'none', pending }
  // 本代已明确有结论（跑完/失败）：失败不自动重试，交给用户点「重试核查」。
  if (localStatus === 'completed' || localStatus === 'failed') return { action: 'none', pending }
  if (localStatus === 'running') return { action: 'resume', pending }
  // 本地无记录时按进入方式区分：刚点开始 ⇒ start；直接打开 ⇒ 只有服务端确实跑过
  // 至少一组才恢复，避免把「用户还没开始」的任务在后台拖起一轮。
  if (autoRun) {
    // 新补件代可先写入沿用组，旧结论因此非空；它不代表待跑组已执行。
    const hasCarriedRun = Array.isArray(runs) && runs.some((run) => (
      run?.state === 'completed' && run.execution_mode === 'carried_forward'
    ))
    if (hasCarriedRun) return { action: 'resume', pending }
    return hasFindings ? { action: 'none', pending } : { action: 'start', pending }
  }
  if (!Array.isArray(runs) || runs.length === 0) return { action: 'none', pending }
  return { action: 'resume', pending }
}
