import { useState } from 'react'
import { ArrowLeft, Download } from 'lucide-react'
import { ReportFrame } from './ReportFrame'
import MaterialPreviewModal from './MaterialPreviewModal'
import { bsMaterialFileUrl } from '../lib/api'

const COLUMNS = ['项目名称', '总包/分包', '合同文件', '合同条款位置', '合同相关摘录', '不符合或风险说明', '依据文件', '依据条款及原文摘录']

export default function RetrospectiveReportView({ report, materials = [], onBack, onExport, exporting }) {
  const [source, setSource] = useState(null)
  const failed = report.execution_status === 'failed'
  const evidence = (report.rows || []).flatMap((row) => row.evidence || [])
  const previewMaterials = [...(report.materials?.length ? report.materials : materials)]
  const roles = Object.fromEntries(previewMaterials.map((item) => [item.material_id, item.role]))
  for (const ref of evidence) {
    if (!previewMaterials.some((item) => Number(item.material_id) === Number(ref.material_id)) && ref.file_name) {
      previewMaterials.push({ material_id: ref.material_id, file_name: ref.file_name })
    }
  }
  const blocks = report.template_document?.blocks || [
    { type: 'heading', text: '核查意见摘要' },
    { type: 'paragraph', text: report.summary || '' },
  ]
  const narrativeBlocks = blocks.filter((block) => block.type !== 'table')
  return <ReportFrame toolbar={<header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[#E5E5E5] pb-4">
    <button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border px-3 py-2 text-sm"><ArrowLeft size={15}/>返回核查</button>
    <h1 className="min-w-0 flex-1 text-base font-semibold">合同倒查审查报告</h1>
    {!failed && <div role="group" aria-label="报告导出" className="flex gap-2">{[['docx', 'Word'], ['pdf', 'PDF']].map(([fmt, label]) =>
      <button key={fmt} type="button" disabled={exporting != null} onClick={() => onExport?.('draft', fmt)} className="inline-flex items-center gap-1 rounded border px-3 py-2 text-sm disabled:opacity-50"><Download size={15}/>导出 {label}</button>)}</div>}
  </header>}>
    <div className="space-y-4">
      {narrativeBlocks.map((block, index) => block.type === 'heading'
        ? <h2 key={`heading-${index}`} className="border-b border-[#DADCE0] pb-2 text-sm font-semibold">{block.text}</h2>
        : <p key={`paragraph-${index}`} className="whitespace-pre-wrap text-sm leading-7 text-[#374151]">{block.text}</p>)}
    </div>
    {!failed && <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[1450px] table-fixed border-collapse text-left text-xs">
      <colgroup>{[7, 5, 10, 10, 20, 22, 10, 16].map((width, i) => <col key={i} style={{ width: `${width}%` }}/>)}</colgroup>
      <thead><tr>{(report.columns || COLUMNS).map((column) => <th key={column} className="border border-[#DADCE0] bg-[#F8F9FA] px-2 py-2 font-semibold">{column}</th>)}</tr></thead>
      <tbody>{report.rows?.map((row) => <tr key={row.finding_id}>{row.cells.map((cell, index) => <td key={index} className="break-words whitespace-pre-wrap border border-[#DADCE0] px-2 py-2 align-top leading-relaxed">
        {index === 3 ? (row.evidence || []).map((ref, j) => <div key={j}><a className="text-[#176B5B] underline" href={`${bsMaterialFileUrl(ref.material_id)}#page=${ref.page}`} target="_blank" rel="noreferrer"
          onClick={(event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
            if (!previewMaterials.some((item) => Number(item.material_id) === Number(ref.material_id))) return
            event.preventDefault(); setSource(ref)
          }}>{roles[ref.material_id] === 'general' ? '总包 · ' : ['sub', 'labor'].includes(roles[ref.material_id]) ? '分包 · ' : ''}{ref.file_name ? `${ref.file_name} · ` : ''}PDF第{ref.page}页{ref.clause ? ` · ${ref.clause}` : ''}</a></div>) : cell}
      </td>)}</tr>)}</tbody>
    </table>{!report.rows?.length && <p className="py-5 text-sm">详见上方综合核查意见。</p>}</div>}
    {source && <MaterialPreviewModal materials={previewMaterials} activeMaterialId={source.material_id} page={source.page} requireParsed={false}
      onSelect={(id) => setSource({ material_id: id, page: null })} onClose={() => setSource(null)}/>}
  </ReportFrame>
}
