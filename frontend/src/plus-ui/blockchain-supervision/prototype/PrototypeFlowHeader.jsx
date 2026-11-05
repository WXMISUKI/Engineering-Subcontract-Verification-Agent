import { buildPrototypeFlow } from './prototypeViewModel'

export default function PrototypeFlowHeader({ taskState, stage, reachedIndex = null, onStageSelect }) {
  const flow = buildPrototypeFlow(taskState, stage, reachedIndex)
  return (
    <ol className="flex list-none items-center border-b border-[#F2EDE5] bg-[#FAFAF7] px-6 py-2" aria-label="审查流程" data-testid="prototype-flow-header">
      {flow.stages.map((stage, index) => (
        <li key={stage.key} className="flex min-w-0 items-center">
          <button
            type="button"
            disabled={!stage.reachable}
            aria-current={stage.state === 'active' ? 'step' : undefined}
            onClick={() => onStageSelect?.(stage.key)}
            className="flex items-center gap-2 rounded px-0.5 py-0.5 text-[15px] disabled:cursor-default"
          >
            <span className={`grid h-[22px] w-[22px] place-items-center rounded-full border-[1.5px] text-[13px] font-bold ${
              stage.state === 'done'
                ? 'border-[#8B7355] bg-[#8B7355] text-white'
                : stage.state === 'active'
                  ? 'border-[#8B7355] bg-white text-[#8B7355] shadow-[0_0_0_3px_#F7F1E9]'
                  : 'border-[#E9E3D9] bg-white text-[#9CA3AF]'
            }`} aria-hidden="true">
              {stage.state === 'done' ? '✓' : index + 1}
            </span>
            <span className={stage.state === 'active' ? 'font-bold text-[#1A1A1A]' : stage.state === 'done' ? 'text-[#6B5A45]' : 'text-[#9CA3AF]'}>
              {stage.label}
            </span>
          </button>
          {index < flow.stages.length - 1 && (
            <span aria-hidden="true" className={`mx-2.5 h-px w-[34px] rounded ${stage.state === 'done' ? 'bg-[#8B7355]' : 'bg-[#E9E3D9]'}`} />
          )}
        </li>
      ))}
    </ol>
  )
}

