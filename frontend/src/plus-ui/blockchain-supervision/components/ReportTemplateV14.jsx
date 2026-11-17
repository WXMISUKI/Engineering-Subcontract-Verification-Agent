import React from 'react'

function ConclusionCard({ text }) {
  const lines = String(text || '').split('\n').filter((line) => line.trim() !== '')
  const [headline, ...rest] = lines
  return <section data-testid="report-v14-conclusion" className="space-y-2 rounded border border-[#D8C7AE] bg-[#FDFAF4] px-4 py-3">
    <p className="text-sm font-semibold text-[#1A1A1A]">{headline}</p>
    {rest.map((line, index) => <p key={index} className="whitespace-pre-wrap text-xs leading-relaxed text-[#5B4A36]">{line}</p>)}
    <p className="text-[11px] text-[#8B7355]">以上为本次核查结果；最终审批意见、补正期限和签字由人工填写。</p>
  </section>
}

export default function ReportTemplateV14({ document }) {
  return <section data-testid="report-template-v14" className="space-y-4 rounded border border-[#E5E5E5] bg-white p-5 text-[13px] leading-relaxed text-[#374151]">
    <h2 className="text-center text-lg font-semibold text-[#1A1A1A]">{document.title}</h2>
    {document.blocks.map((block, index) => {
      if (block.type === 'page_break') return <div key={index} className="my-4 border-t border-dashed border-[#D1D5DB]" />
      if (block.type === 'conclusion') return <ConclusionCard key={index} text={block.text} />
      if (block.type === 'heading') return <h3 key={index} className="pt-3 text-sm font-semibold text-[#1A1A1A]">{block.text}</h3>
      if (block.type === 'table') return <div key={index} className="overflow-x-auto">
        <table className="w-full min-w-[38rem] table-fixed border-collapse text-xs">
          <colgroup>{block.widths.map((ratio, col) => <col key={col} style={{ width: `${ratio * 100}%` }} />)}</colgroup>
          <thead><tr>{block.headers.map((text, col) => <th key={col} scope="col" className="border border-[#D1D5DB] bg-[#F7F7F5] p-2 text-left font-medium">{text}</th>)}</tr></thead>
          <tbody>{block.rows.map((row, number) => <tr key={number}>{row.map((value, col) => <td key={col} className="whitespace-pre-wrap break-words border border-[#D1D5DB] p-2 align-top">{value}</td>)}</tr>)}</tbody>
        </table>
      </div>
      return <p key={index} className={`whitespace-pre-wrap break-words ${block.bold ? 'font-semibold text-[#1A1A1A]' : ''} ${block.align === 'center' ? 'text-center' : ''} ${block.indent ? 'pl-4' : ''}`}>{block.text}</p>
    })}
  </section>
}
