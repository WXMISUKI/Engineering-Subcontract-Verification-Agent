export function aiReviewRequiresNewRun(error) {
  return error?.status === 409 && error?.detail === '核查材料、结论或依据授权已变化，请刷新并重新核查'
}

function validateStatus(status, runId) {
  if (status?.run_id !== runId) throw new Error('核查运行已变化，请刷新后重试')
  const keys = ['not_started', 'pending', 'completed', 'partial', 'failed', 'not_run']
  if (!keys.every((key) => Number.isInteger(status.counts?.[key]) && status.counts[key] >= 0) ||
      !Number.isInteger(status.skipped) || status.skipped < 0) throw new Error('AI 核查状态不完整，请刷新后重试')
  if (status.reviewable_partial != null && (!Number.isInteger(status.reviewable_partial) ||
      status.reviewable_partial < 0 || status.reviewable_partial > status.counts.partial)) throw new Error('AI 核查状态不完整，请刷新后重试')
  if (status.reviewable_not_run != null && (!Number.isInteger(status.reviewable_not_run) ||
      status.reviewable_not_run < 0 || status.reviewable_not_run > status.counts.not_run)) throw new Error('AI 核查状态不完整，请刷新后重试')
  if (status.reviewable_failed != null && (!Number.isInteger(status.reviewable_failed) ||
      status.reviewable_failed < 0 || status.reviewable_failed > status.counts.failed)) throw new Error('AI 核查状态不完整，请刷新后重试')
  return status
}

/** 依据服务端状态按组启动；失败和部分完成仅在用户重试时继续。 */
export async function runAutoAiReview({ taskId, runs, groups, getStatus, runBatch, retry = false, onProgress = () => {} }) {
  const selected = groups.map((groupId) => runs.find((run) => run.group_id === groupId && run.state === 'completed'))
  if (selected.some((run) => !run?.run_id)) throw new Error('核查步骤尚未完成，请刷新后重试')
  let warning = false
  let pending = false
  let failed = false
  const failureCategories = new Set()
  for (let index = 0; index < selected.length; index += 1) {
    const run = selected[index]
    onProgress(index, selected.length, run.group_id, 'status')
    let status = validateStatus(await getStatus(taskId, run.group_id), run.run_id)
    if (status.counts.pending > 0) { pending = true; continue }
    const retryable = status.counts.failed + status.counts.partial - (status.reviewable_partial ?? 0)
      + status.counts.not_run - (status.reviewable_not_run ?? 0)
    if (status.counts.not_started > 0 || (retry && retryable > 0)) {
      onProgress(index, selected.length, run.group_id, 'processing')
      await runBatch(taskId, run.group_id, run.run_id, { resumePartial: retry })
      status = validateStatus(await getStatus(taskId, run.group_id), run.run_id)
    }
    if (status.counts.pending > 0) { pending = true; continue }
    if (status.counts.not_started > 0 || status.counts.failed > (status.reviewable_failed ?? 0) || status.counts.not_run > (status.reviewable_not_run ?? 0) || status.counts.partial > (status.reviewable_partial ?? 0)) {
      failed = true
      if (Array.isArray(status.failure_categories)) status.failure_categories.forEach((category) => failureCategories.add(category))
      continue
    }
    warning ||= status.skipped > 0 || status.counts.partial > 0 || status.counts.not_run > 0 || status.counts.failed > 0
  }
  const result = { state: pending ? 'pending' : failed ? 'failed' : 'completed', warning: warning || pending || failed }
  if (failed && failureCategories.size) result.failureCategories = [...failureCategories]
  return result
}
