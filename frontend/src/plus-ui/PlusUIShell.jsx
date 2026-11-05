export default function PlusUIShell({ title, subtitle, actions, children, headerModelSelector, sidebarOpen, onToggleSidebar, dataTestId, moduleCode, ariaLabel }) {
  return <div className="flex h-full min-h-0 flex-col bg-[#fafafa]" data-testid={dataTestId} data-plus-module={moduleCode} aria-label={ariaLabel}>
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-gray-200 bg-white px-6">
      <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-accent" /><span className="whitespace-nowrap text-sm font-semibold text-primary">{title}</span>
      {subtitle && <small className="hidden whitespace-nowrap text-tertiary lg:inline">· {subtitle}</small>}
      <div className="flex-1" />{headerModelSelector}{actions}
      {onToggleSidebar && <button onClick={onToggleSidebar} className="min-h-11 px-2 text-sm text-secondary">{sidebarOpen ? '收起任务列表' : '展开任务列表'}</button>}
    </header>
    <div className="flex min-h-0 flex-1 flex-col">{children}</div>
  </div>
}
