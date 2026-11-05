/**
 * 工程分包审查专属工作台框架。
 * 系统级侧栏由 MainLayout/Sidebar 提供；PlusUIShell 提供平台顶栏；
 * 此框架只提供 BS 的中央业务页面承载区，避免在 Plus 内重复造宿主侧栏。
 */
export default function BlockchainSupervisionWorkspaceFrame({
  phase = 'loading',
  children,
}) {
  return (
    <div data-testid="bs-workspace-frame" data-workspace-phase={phase} className="flex min-w-0 min-h-0 flex-1 overflow-hidden bg-[#F4F7FA]">
      <main data-testid="bs-workspace-main" className="flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden">
        {children}
      </main>
    </div>
  )
}
