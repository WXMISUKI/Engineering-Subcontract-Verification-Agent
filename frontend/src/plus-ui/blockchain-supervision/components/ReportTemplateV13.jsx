import React from 'react'

export default function ReportTemplateV13({ document }) {
  return <section data-testid="report-template-v13" className="space-y-4 rounded border border-[#E5E5E5] bg-white p-5 text-[13px] leading-relaxed text-[#374151]">
    <h2 className="text-center text-lg font-semibold text-[#1A1A1A]">{document.title}</h2>
    <p className="text-xs text-[#6B7280]">V1.3 试用 · 人工审批意见、期限及签字请在导出 Word 中填写。</p>
    {document.blocks.map((block, index) => {
      if (block.type === 'page_break') return <div key={index} className="h-3" />
      if (block.type === 'heading') return <h3 key={index} className="pt-3 text-sm font-semibold text-[#1A1A1A]">{block.text}</h3>
      if (block.type === 'table') return <div key={index} className="overflow-x-auto">
        <table className="w-full min-w-[36rem] table-fixed border-collapse text-xs">
          <colgroup>{block.widths.map((ratio, col) => <col key={col} style={{ width: `${ratio * 100}%` }} />)}</colgroup>
          <thead><tr>{block.headers.map((text, col) => <th key={col} scope="col" className="border border-[#D1D5DB] bg-[#F7F7F5] p-2 text-left font-medium">{text}</th>)}</tr></thead>
          <tbody>{block.rows.map((row, number) => <tr key={number}>{row.map((value, col) => <td key={col} className="whitespace-pre-wrap break-words border border-[#D1D5DB] p-2 align-top">{value}</td>)}</tr>)}</tbody>
        </table>
      </div>
      return <p key={index} className="whitespace-pre-wrap break-words">{block.text}</p>
    })}
  </section>
}
