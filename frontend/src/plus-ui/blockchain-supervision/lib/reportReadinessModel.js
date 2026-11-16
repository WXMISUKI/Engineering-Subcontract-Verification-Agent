/** 任务详情为当前门禁权威；旧服务/未知协议不会默认开放正式输出。 */
export function normalizeReportReadiness(dto) {
  const valid = dto?.schema_version === 'bs.report-readiness.v1'
    && ['draft_readable', 'confirmed_output_ready', 'task_close_ready'].every((key) => typeof dto[key] === 'boolean')
    && ['completed', 'stale', 'not_started', 'running', 'incomplete', 'unknown'].includes(dto.execution?.state)
    && ['complete', 'pending', 'unknown'].includes(dto.reading_quality?.state)
    && Array.isArray(dto.reading_quality?.pages) && Array.isArray(dto.finding_review?.blocking)
    && Array.isArray(dto.blocking_reasons) && Array.isArray(dto.next_actions)
    && (!dto.confirmed_output_ready || (dto.execution.state === 'completed'
      && dto.reading_quality.state !== 'pending' && dto.finding_review.blocking.length === 0))
  if (valid) return dto
  return { draft_readable: false, confirmed_output_ready: false, task_close_ready: false,
    blocking_reasons: [{ code: 'readiness_unavailable', message: '报告就绪状态尚未确认，请刷新任务后重试。' }] }
}
