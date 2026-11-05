// 待办与处置分组（V2-b）：纯展示组件。
// - 只吃 props（groups / selectedKey / onSelect / onSelectItem），不请求、不判定，
//   不 import 后端映射常量；分组和计数由上游派生层给出，条目只呈现标题与必要的已处置审计标记。
// - 机器三态与严重度不在缩略项重复展示，详细依据、证据和人工动作由选中详情承载。
// - reviewed === true 的条目整体弱化，并保留「已处置」文字标记。
// - 每组最多列出 5 条，列表 max-h 内滚动；窄栏纵向堆叠、根与条目均 min-w-0，不横向溢出。

const MAX_ITEMS_PER_GROUP = 5

function TodoItem({ item, onSelectItem }) {
  const findingId = item.findingId
  const reviewed = item.reviewed === true

  return (
    <button
      type="button"
      data-testid={`todo-item-${findingId}`}
      data-reviewed={reviewed}
      onClick={() => onSelectItem?.(findingId)}
      className="flex w-full min-w-0 flex-col gap-1 border-b border-[#E8EEF4] px-2.5 py-2 text-left last:border-b-0 hover:bg-[#F8FAFC]"
    >
      <span
        className={`min-w-0 truncate text-[12px] font-medium ${reviewed ? 'text-[#94A3B8]' : 'text-[#16283D]'}`}
        title={item.title}
      >
        {item.title}
      </span>

      <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-[10px]">
        {reviewed && (
          <span
            data-testid={`todo-reviewed-${findingId}`}
            className="rounded-full border border-[#E2E8F0] bg-[#F8FAFC] px-1.5 py-0.5 text-[#94A3B8]"
          >
            已处置
          </span>
        )}
      </span>
    </button>
  )
}

export default function TodoGroups({
  groups = [],
  selectedKey = null,
  onSelect,
  onSelectItem,
  className = '',
}) {
  const visibleGroups = (Array.isArray(groups) ? groups : []).filter(Boolean)

  return (
    <section
      data-testid="todo-groups"
      aria-label="待办与处置"
      className={`flex w-full min-w-0 flex-col ${className}`}
    >
      <h2 className="text-[13px] font-semibold text-[#16283D]">待办与处置</h2>

      {visibleGroups.length === 0 ? (
        <p
          data-testid="todo-groups-empty"
          className="mt-2 rounded-[6px] border border-dashed border-[#D8E1EB] bg-[#F8FAFC] px-3 py-6 text-center text-[12px] text-[#64748B]"
        >
          暂无待办
        </p>
      ) : (
        <div className="mt-2 min-w-0 space-y-2">
          {visibleGroups.map((group, position) => {
            const groupKey = group.key ?? `group-${position + 1}`
            const allItems = (Array.isArray(group.items) ? group.items : []).filter(Boolean)
            const visibleItems = allItems.slice(0, MAX_ITEMS_PER_GROUP)
            const selected = selectedKey === groupKey
            const count = group.count ?? allItems.length

            return (
              <section
                key={groupKey}
                data-testid={`todo-group-section-${groupKey}`}
                className={`min-w-0 overflow-hidden rounded-[6px] border ${
                  selected ? 'border-[#B8C9D9] bg-[#F8FAFC]' : 'border-[#D8E1EB] bg-white'
                }`}
              >
                <button
                  type="button"
                  data-testid={`todo-group-${groupKey}`}
                  aria-pressed={selected}
                  onClick={() => onSelect?.(selected ? null : groupKey)}
                  className={`flex min-h-9 w-full min-w-0 items-center gap-2 px-2.5 text-left text-[12px] transition-colors ${
                    selected
                      ? 'bg-[#EAF2FB] font-semibold text-[#143B63]'
                      : 'text-[#385A77] hover:bg-[#F8FAFC]'
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate" title={group.label}>{group.label}</span>
                  <span className="shrink-0 rounded-full border border-[#D8E1EB] bg-white px-1.5 py-0.5 text-[10px] tabular-nums text-[#64748B]">
                    {count}
                  </span>
                </button>

                {group.nextStep && (
                  <p
                    data-testid={`todo-next-${groupKey}`}
                    className="border-t border-[#EEF3F8] bg-[#F8FAFC] px-2.5 py-1 text-[10px] leading-relaxed text-[#64748B]"
                  >
                    {group.nextStep}
                  </p>
                )}

                <div
                  data-testid={`todo-group-items-${groupKey}`}
                  className="max-h-[168px] min-w-0 overflow-y-auto overflow-x-hidden border-t border-[#E8EEF4]"
                >
                  {visibleItems.length === 0 ? (
                    <p
                      data-testid={`todo-group-empty-${groupKey}`}
                      className="px-2.5 py-3 text-center text-[11px] text-[#94A3B8]"
                    >
                      暂无
                    </p>
                  ) : (
                    visibleItems.map((item) => (
                      <TodoItem key={item.findingId} item={item} onSelectItem={onSelectItem} />
                    ))
                  )}
                </div>
              </section>
            )
          })}
        </div>
      )}
    </section>
  )
}
