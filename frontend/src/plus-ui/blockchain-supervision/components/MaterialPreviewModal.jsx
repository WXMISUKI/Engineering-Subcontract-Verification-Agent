import { useEffect, useRef } from 'react'
import { Download, FileText, X } from 'lucide-react'
import StructuralPdfPreview from '../../structural-check/components/StructuralPdfPreview'
import { bsMaterialFileUrl } from '../lib/api'
import { collectContainerIds } from '../lib/materialState'

function downloadUrl(id) {
  const url = bsMaterialFileUrl(id)
  return `${url}${url.includes('?') ? '&' : '?'}download=1`
}

function materialId(material) {
  return material?.id ?? material?.material_id
}

function materialFileName(material) {
  const value = typeof material?.file_name === 'string' ? material.file_name : ''
  // 上传阶段的原型行会把文件大小拼到展示文本的第二行；预览判定必须使用原始文件名。
  return value.split(/\r?\n/, 1)[0].trim()
}

/** 单实例材料预览层：切换文件不会追加页面，关闭后完全卸载预览。 */
export default function MaterialPreviewModal({ materials = [], activeMaterialId, page = null, fullPage = false, containerIds: declaredContainerIds = [], requireParsed = true, onSelect, onClose, onError, evidenceChoices = [], onSelectEvidence, reviewDetails = null }) {
  const materialList = Array.isArray(materials) ? materials : []
  const storedContainerIds = materialList
    .filter((item) => item?.parse_state === 'stored')
    .map((item) => materialId(item))
  const containerIdSet = new Set([
    ...collectContainerIds(materialList, [...storedContainerIds, ...declaredContainerIds]),
  ].map((id) => String(id)))
  const previewableMaterials = materialList.filter((item) => {
    const id = materialId(item)
    const fileName = materialFileName(item)
    return (!requireParsed || item?.parse_state === 'done') && /\.pdf$/i.test(fileName) && !containerIdSet.has(String(id))
  })
  const material = previewableMaterials.find((item) => String(materialId(item)) === String(activeMaterialId)) || previewableMaterials[0]
  const closeRef = useRef(null)
  const returnFocusRef = useRef(null)

  useEffect(() => {
    if (!material && !reviewDetails) return undefined
    returnFocusRef.current = document.activeElement
    closeRef.current?.focus?.()
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose?.()
        return
      }
      if (event.key !== 'Tab') return
      const dialog = closeRef.current?.closest('[role="dialog"]')
      const focusables = Array.from(dialog?.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') || [])
        .filter((node) => !node.disabled && !node.hidden)
      if (!focusables.length) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      returnFocusRef.current?.focus?.()
    }
  }, [material, onClose, reviewDetails])

  if (!material && !reviewDetails) return null
  if (!material) return <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true" aria-label="材料核实说明"><section className="w-full max-w-lg rounded border border-[#D8DEE8] bg-white p-4"><h3 className="text-sm font-medium">待核实原因</h3><p className="mt-2 text-xs text-[#4B5563]">{reviewDetails.reason}</p><div className="mt-4 flex gap-2">{reviewDetails.onSupplement && <button type="button" onClick={reviewDetails.onSupplement} className="rounded border px-3 py-1 text-xs">补件</button>}<button ref={closeRef} type="button" onClick={onClose} className="rounded border px-3 py-1 text-xs">关闭</button></div></section></div>
  const hasPage = Number.isFinite(Number(page)) && Number(page) > 0

  return (
    <div className={fullPage ? 'flex min-h-0 flex-1 flex-col' : 'fixed inset-0 z-[80] flex items-center justify-center bg-black/45 p-4'} role="dialog" aria-modal="true" aria-label="材料预览">
      <section data-testid="material-preview-modal" className={`flex min-h-0 w-full min-w-0 flex-col overflow-hidden border border-[#D8DEE8] bg-white ${fullPage ? 'flex-1 rounded-[6px]' : 'h-[min(88vh,820px)] max-w-5xl rounded-[8px] shadow-2xl'}`}>
        <header className="flex min-h-12 shrink-0 items-center gap-3 border-b border-[#E5E7EB] bg-[#F8FAFC] px-4">
          <FileText className="h-4 w-4 shrink-0 text-[#8B7355]" />
          <select value={materialId(material)} onChange={(event) => onSelect?.(Number(event.target.value))} aria-label="切换预览文件" className="min-w-0 max-w-[min(55vw,520px)] flex-1 truncate rounded-[5px] border border-[#E5E7EB] bg-white px-2 py-1.5 text-[13px] font-semibold text-[#1A1A1A]">
            {previewableMaterials.map((item) => <option key={materialId(item)} value={materialId(item)}>{item.file_name}</option>)}
          </select>
          <a href={downloadUrl(materialId(material))} download={material.file_name} className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-[5px] border border-[#E5E7EB] px-2.5 text-xs text-[#6B7280] hover:border-[#8B7355] hover:text-[#8B7355]" title="下载当前文件">
            <Download className="h-3.5 w-3.5" />下载
          </a>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭预览" className="inline-flex min-h-9 min-w-9 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#6B7280] hover:border-[#8B7355] hover:text-[#8B7355]">
            <X className="h-4 w-4" />
          </button>
        </header>
        {reviewDetails && <section className="shrink-0 border-b border-[#E5E7EB] px-4 py-2" aria-label="材料核实说明"><p className="text-xs font-medium text-[#705C45]">待核实原因</p><p className="mt-1 text-xs text-[#4B5563]">{reviewDetails.reason}</p><div className="mt-2 flex gap-2">{reviewDetails.onConfirm && <><button type="button" onClick={() => reviewDetails.onConfirm('conforming')} className="rounded border px-3 py-1 text-xs">符合</button><button type="button" onClick={() => reviewDetails.onConfirm('nonconforming')} className="rounded border px-3 py-1 text-xs">不符合</button></>}{reviewDetails.onSupplement && <button type="button" onClick={reviewDetails.onSupplement} className="rounded border px-3 py-1 text-xs">补件</button>}</div></section>}
        {evidenceChoices.length > 1 && <div className="flex shrink-0 flex-wrap gap-2 border-b border-[#E5E7EB] px-4 py-2" aria-label="关联原文位置">{evidenceChoices.map((choice) => <button type="button" key={`${choice.materialId}:${choice.page}`} onClick={() => onSelectEvidence?.(choice)} className="rounded border border-[#D8CBB8] px-2 py-1 text-xs text-[#705C45]">{choice.fileName} · 第 {choice.page} 页</button>)}</div>}
        <div className="min-h-0 flex-1 bg-[#EEF1F5]">
          <StructuralPdfPreview key={evidenceChoices.length > 1 ? `${materialId(material)}:${page}` : materialId(material)} src={bsMaterialFileUrl(materialId(material))} fileName={material.file_name} initialPage={hasPage ? Number(page) : 1} onError={onError} />
        </div>
      </section>
    </div>
  )
}
