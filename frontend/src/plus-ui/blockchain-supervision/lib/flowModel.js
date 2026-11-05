// 原型优先阶段流纯派生层：只消费后端任务状态/五步快照，不执行核查、不伪造进度。
import { isMaterialOnly } from './reviewDecision'

export const FLOW_STAGES = [
  { key: 'upload', label: '上传材料' },
  { key: 'run', label: '智能核查' },
  { key: 'review', label: '人工复核' },
  { key: 'report', label: '审查报告' },
]

const STAGE_INDEX = Object.fromEntries(FLOW_STAGES.map((stage, index) => [stage.key, index]))

export function stageOfTaskState(state) {
  if (state === 'done') return 'report'
  if (state === 'review') return 'review'
  if (state === 'running') return 'run'
  return 'upload'
}

export function buildFlowState({ taskState, snapshot, findings = [] } = {}) {
  const current = stageOfTaskState(taskState)
  const currentIndex = STAGE_INDEX[current]
  const steps = Array.isArray(snapshot?.steps) ? snapshot.steps : []
  const completedSteps = steps.filter((step) => step?.state === 'completed').length
  const selectedSteps = steps.filter((step) => step?.state !== 'out_of_scope').length
  const issueCount = Array.isArray(findings)
    ? findings.filter((finding) => finding?.open_term !== true && !isMaterialOnly(finding) && (finding?.status === 'hit' || finding?.status === 'undecidable')).length
    : 0

  return {
    current,
    stages: FLOW_STAGES.map((stage, index) => ({
      ...stage,
      state: index < currentIndex ? 'completed' : index === currentIndex ? 'current' : 'upcoming',
    })),
    progress: {
      completedSteps,
      selectedSteps,
      percent: selectedSteps > 0 ? Math.round((completedSteps / selectedSteps) * 100) : 0,
    },
    issueCount,
  }
}
