import FiveStepCards from './FiveStepCards'

export default function ReviewPrototypeLeftRail({
  steps = [],
  selectedStepId = null,
  onSelectStep,
  loading = false,
}) {
  return (
    <div data-testid="review-prototype-left-rail" className="flex min-w-0 flex-col gap-3 rounded-[6px] border border-[#E5DED3] bg-[#FBF5EC] p-3">
      <FiveStepCards
        steps={steps}
        loading={loading}
        selectedStepId={selectedStepId}
        onSelectStep={onSelectStep}
        compact
      />
    </div>
  )
}
