/**
 * 工程分包审查智能体专属页面容器。
 *
 * It is the only page outlet between the Plus platform shell and the business
 * pages. Overview, materials, run, review, report, and future overlays must be
 * mounted below this boundary so legacy nodes cannot leak into the host.
 */
export default function BlockchainSupervisionModuleContainer({ phase = 'loading', children }) {
  return (
    <section
      data-testid="bs-module-container"
      data-module-phase={phase}
      aria-label="工程分包审查智能体专属工作台"
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#F4F7FA]"
    >
      <div data-testid="bs-module-page-outlet" className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    </section>
  )
}
