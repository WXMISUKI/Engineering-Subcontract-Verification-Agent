const WORKSPACES = [
  { key: 'overview', label: '五步总览' },
  { key: 'materials', label: '材料清单' },
  { key: 'review', label: '发现的问题' },
  { key: 'report', label: '报告' },
]

function countText(value, key) {
  if (value == null || value === '') return ''
  return String(value)
}

export default function PrototypeWorkspaceNav({ activeKey = null, counts = {}, onSelect, visuallyHidden = false }) {
  return (
    <nav
      data-testid="prototype-workspace-nav"
      aria-label="工作空间"
      className={visuallyHidden
        ? 'sr-only'
        : 'flex shrink-0 items-center gap-1 overflow-x-auto border-b border-[#DCE5EE] bg-transparent px-0 py-0'}
    >
      {WORKSPACES.map((workspace) => {
        const active = activeKey === workspace.key
        const count = countText(counts[workspace.key], workspace.key)
        return (
          <button
            key={workspace.key}
            type="button"
            data-testid={`workspace-${workspace.key}`}
            aria-current={active ? 'page' : undefined}
            onClick={() => onSelect?.(workspace.key)}
            className={`shrink-0 border-0 border-b-[3px] bg-transparent px-3.5 py-2.5 text-[14px] font-semibold transition-colors ${
              active
                ? 'border-b-[#174C7E] text-[#174C7E]'
                : 'border-b-transparent text-[#587089] hover:bg-transparent hover:text-[#174C7E]'
            }`}
          >
            {workspace.label}
            {count && <span className="ml-1.5 inline-flex min-w-[20px] justify-center rounded-full bg-[#EAF2FB] px-1.5 text-[11px] tabular-nums">{count}</span>}
          </button>
        )
      })}
    </nav>
  )
}
