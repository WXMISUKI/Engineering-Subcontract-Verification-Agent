// 两种审查共用报告容器；业务内容、导出资格与动作由各自保存结果决定。
export function ReportFrame({ prototypeMode = false, toolbar, children }) {
  return <div data-testid="report-frame" className={`flex h-full min-h-0 flex-1 flex-col ${prototypeMode ? 'p-0' : 'px-8 py-6'}`}>
    {toolbar}
    <main className={`min-h-0 flex-1 ${prototypeMode ? 'overflow-visible py-0' : 'overflow-y-auto py-4'}`}>{children}</main>
  </div>
}
