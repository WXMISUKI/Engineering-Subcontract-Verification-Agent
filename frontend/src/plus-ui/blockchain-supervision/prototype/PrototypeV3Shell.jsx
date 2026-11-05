import PrototypeFlowHeader from './PrototypeFlowHeader'

// v3 页面级外壳：流程条只在这里挂载一次，业务工作区作为子内容注入。
// 任务头、工作区导航和各工作区仍由调用方按原型顺序提供，避免外壳复制业务状态。
export default function PrototypeV3Shell({
  taskState,
  stage,
  reachedIndex = null,
  onStageSelect,
  children,
}) {
  return (
    <div data-testid="prototype-v3-shell" className="bs-v3 flex h-full min-h-0 flex-1 flex-col overflow-hidden">
      <PrototypeFlowHeader
        taskState={taskState}
        stage={stage}
        reachedIndex={reachedIndex}
        onStageSelect={onStageSelect}
      />
      {children}
    </div>
  )
}
