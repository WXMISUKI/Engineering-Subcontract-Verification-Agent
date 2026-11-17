function WorksheetTable({ headers = [], rows = [] }) {
  if (!headers.length || !rows.length) return null
  return <div className="mt-2 overflow-x-auto">
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr>{headers.map((header) => <th key={header} className="border border-[#D1D5DB] bg-[#F7F7F5] p-2 text-left font-medium">{header}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, index) => <tr key={index}>
          {row.map((value, column) => <td key={column} className="whitespace-pre-wrap break-words border border-[#D1D5DB] p-2 align-top">{value}</td>)}
        </tr>)}
      </tbody>
    </table>
  </div>
}

/**
 * V1.4 八张检查表工作底稿（系统查看）。
 * 只读展示后端冻结的 attachments，不并入正式审查意见，也不重新计算结论。
 */
export default function ReportWorksheetV14({ attachments = [] }) {
  if (!Array.isArray(attachments) || attachments.length === 0) return null
  return <details data-testid="report-v14-worksheet" className="mt-5 rounded border border-[#E5E5E5] bg-[#FAFAF7] px-4 py-3 text-[12px] leading-relaxed text-[#4B5563]">
    <summary className="cursor-pointer font-semibold text-[#1A1A1A]">8 表工作底稿（系统查看，不进入正式审查意见）</summary>
    <p className="mt-2 text-[11px] text-[#6B7280]">
      以下为八张检查表的核查记录和人工核查事项，供审查人核对留档；正式意见以正文第 7 章结论和附件二导航为准。
    </p>
    {attachments.map((sheet, index) => <section key={sheet.code || index} className="mt-4 border-t border-[#E5E5E5] pt-3">
      <h3 className="text-[13px] font-semibold text-[#1A1A1A]">{sheet.title || `检查表 ${index + 1}`}</h3>
      {sheet.note && <p className="mt-1 text-[11px] text-[#6B7280]">{sheet.note}</p>}
      <WorksheetTable headers={sheet.auto_headers || []} rows={sheet.auto_rows || []} />
      {Array.isArray(sheet.manual_rows) && sheet.manual_rows.length > 0 && <div className="mt-3">
        <p className="text-[11px] font-medium text-[#92400E]">需人工核查事项</p>
        <WorksheetTable headers={sheet.manual_headers || []} rows={sheet.manual_rows} />
      </div>}
      {sheet.kind === 'key_value' && Array.isArray(sheet.rows) && sheet.rows.length > 0 && (
        <WorksheetTable headers={['项目', '内容']} rows={sheet.rows} />
      )}
    </section>)}
  </details>
}
