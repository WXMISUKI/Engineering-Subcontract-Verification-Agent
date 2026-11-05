import { ChevronDown, Download, FileText, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import StructuralPdfPreview from '../../structural-check/components/StructuralPdfPreview'
import { bsMaterialFileUrl, getBsMaterialPageText } from '../lib/api'

function displayOcrText(value) {
  const text = String(value || '')
  if (!/<table(?:\s|>)/i.test(text)) return text
  // OCR 可能返回 HTML 表格；只转换为纯文本，绝不将材料内容作为 HTML 执行。
  return text.replace(/```(?:html)?/gi, '')
    .replace(/<\s*br\s*\/?\s*>/gi, '\n')
    .replace(/<\s*\/\s*t[dh]\s*>/gi, '\t')
    .replace(/<\s*\/\s*tr\s*>/gi, '\n')
    .replace(/<\/?[a-z][^>]*>/gi, '')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&amp;/gi, '&')
    .replace(/\n[\t ]*\n/g, '\n').trim()
}

// 只忽略空白差异，保留页面原文与原有换行；非唯一匹配不推测位置。
function quoteRange(text, quote) {
  if (!quote) return null
  const exact = text.indexOf(quote)
  if (exact >= 0) return text.indexOf(quote, exact + 1) < 0 ? [exact, exact + quote.length] : null
  let normalized = ''
  const offsets = []
  for (let index = 0; index < text.length; index += 1) {
    if (!/\s/u.test(text[index])) {
      normalized += text[index]
      offsets.push(index)
    }
  }
  const needle = quote.replace(/\s/gu, '')
  if (!needle) return null
  const start = normalized.indexOf(needle)
  if (start < 0 || normalized.indexOf(needle, start + 1) >= 0) return null
  return [offsets[start], offsets[start + needle.length - 1] + 1]
}

function PageTextPanel({ materialId, page, refs }) {
  const [open, setOpen] = useState(true)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return undefined
    const controller = new AbortController()
    setResult(null)
    setError('')
    getBsMaterialPageText(materialId, page, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return
        if (String(data?.material_id) !== String(materialId) || data?.page !== page || typeof data?.text !== 'string') {
          setError('识别文字与当前材料或页码不一致，请查看原件。')
          return
        }
        setResult(data)
      })
      .catch(() => { if (!controller.signal.aborted) setError('本页识别文字暂不可用，请查看原件。') })
    return () => controller.abort()
  }, [open, materialId, page])
  const text = displayOcrText(result?.text)
  const range = refs.map((ref) => quoteRange(text, displayOcrText(ref?.quote))).find(Boolean)
  return (
    <div className="shrink-0 border-t border-[#EFE6D8] px-3 py-2 text-xs">
      <button type="button" onClick={() => setOpen(!open)} className="text-[#8B7355] hover:underline">
        {open ? '收起本页识别文字' : '查看本页识别文字'}
      </button>
      {open && <div className="mt-2 max-h-48 overflow-auto" aria-live="polite">
        {error || (!result ? '正在读取识别文字…' : <>
          <p className="mb-1 text-[#6B7280]">第 {page} 页识别文字{result.readable === false ? ' · 识别质量待核，请对照原件' : ''}</p>
          <p className="whitespace-pre-wrap break-words">{!range ? (text || '本页暂无识别文字') : <>{text.slice(0, range[0])}<mark>{text.slice(range[0], range[1])}</mark>{text.slice(range[1])}</>}</p>
          {refs.some((ref) => ref?.quote) && !range && <p className="mt-1 text-[#6B7280]">摘录未能在本页识别文字中唯一匹配，请对照原件。</p>}
        </>)}
      </div>}
    </div>
  )
}

function matchingReferences(finding, materialId, page) {
  const evidence = Array.isArray(finding?.evidence) ? finding.evidence : []
  // 证据列表存在时只消费与当前材料、页码一致的引用，避免继承其他引用的框。
  const refs = evidence.length ? evidence : [finding]
  return refs.filter((ref) => {
    const refMaterialId = ref?.material_id ?? ref?.materialId
    return refMaterialId != null && String(refMaterialId) === String(materialId) && Number(ref?.page) === Number(page)
  })
}

function pdfAnchorRects(refs) {
  for (const ref of refs) {
    const anchor = ref?.anchor
    if (anchor?.coordinate_space !== 'pdf_page' || anchor?.coordinate_frame !== 'rendered_page' || !Array.isArray(anchor.bbox) || anchor.bbox.length !== 4) continue
    const rect = anchor.bbox.map(Number)
    if (rect.every(Number.isFinite) && rect[2] > rect[0] && rect[3] > rect[1]) return [rect]
  }
  return []
}

function materialOptionLabel(option, duplicateCount) {
  const name = option.file_name || '未命名材料'
  if (duplicateCount <= 1) return name
  const revision = option.revision_no != null ? `第 ${option.revision_no} 版` : null
  const subject = option.subject_id != null ? `主体 ${option.subject_id}` : null
  const identity = [revision, subject, `材料 #${option.id ?? option.material_id ?? '—'}`]
    .filter(Boolean).join(' · ')
  return `${name}（${identity}）`
}

/**
 * 当前工作台内的原文预览。PDF 组件本身负责加载、页码跳转和可选高亮；
 * 只有后端给出 precise/page 线索时才向用户承诺定位能力。
 */
export default function MaterialPreviewPane({
  material,
  page = null,
  finding = null,
  materials = [],
  onMaterialChange,
  onClose,
  onError,
  emptyMessage = '点击问题中的“定位原文”，这里会显示对应材料原文',
  // 刀D（设计 104 §4.3）：对照模式/大栏预览按容器宽度自适应，避免固定 110% 两侧灰边。
  fitWidth = false,
}) {
  const [viewPage, setViewPage] = useState(null)
  const visiblePage = viewPage?.materialId === material?.id && viewPage?.sourcePage === page
    ? viewPage.visiblePage : (Number(page) || 1)
  const materialOptions = Array.isArray(materials) ? materials : Object.values(materials || {})
  const materialNameCounts = materialOptions.reduce((counts, option) => {
    const name = option.file_name || '未命名材料'
    counts[name] = (counts[name] || 0) + 1
    return counts
  }, {})
  if (!material) {
    return (
      <section data-testid="material-preview-empty" aria-label="原文预览" className="flex min-h-[360px] min-w-0 flex-1 items-center justify-center rounded-[6px] border border-[#EFE6D8] bg-white px-6 text-center">
        <div className="max-w-sm text-[13px] text-[#9CA3AF]">
          <FileText className="mx-auto mb-2 h-6 w-6 text-[#C8B89E]" />
          {emptyMessage}
        </div>
      </section>
    )
  }

  const standalone = !finding
  const refs = matchingReferences(finding, material.id ?? material.material_id, page)
  const matchedAnchor = refs.find((ref) => ref?.anchor)?.anchor
  const anchorMode = matchedAnchor?.status === 'precise' ? 'precise' : matchedAnchor?.status === 'page_only' ? 'page' : null
  const locationMode = finding?.location_mode ?? finding?.locationMode ?? anchorMode ?? (page ? 'page' : null)
  const precise = locationMode === 'precise'
  const hasPage = Number.isFinite(Number(page)) && Number(page) > 0
  const anchorRects = precise ? pdfAnchorRects(refs) : []
  const activeFinding = precise
    ? { ...finding, page: Number(page), rects: anchorRects.length ? anchorRects : undefined }
    : null

  return (
    <section data-testid="material-preview" data-material-id={String(material.id)} aria-label="原文预览" className="flex min-h-[360px] min-w-0 flex-1 flex-col overflow-hidden rounded-[6px] border border-[#EFE6D8] bg-white">
      <header className="flex min-h-11 items-center gap-2 border-b border-[#EFE6D8] px-3">
        <FileText className="h-4 w-4 shrink-0 text-[#8B7355]" />
        <div className="min-w-0 flex-1">
          {materialOptions.length > 1 && onMaterialChange ? (
            <label className="relative block min-w-0">
              <span className="sr-only">选择预览材料</span>
              <select
                aria-label="选择预览材料"
                value={String(material.id)}
                onChange={(event) => onMaterialChange(event.target.value)}
                className="w-full appearance-none truncate bg-transparent pr-5 text-xs font-semibold text-[#1A1A1A] outline-none"
              >
                {materialOptions.map((option) => (
                  <option key={option.id} value={option.id}>{materialOptionLabel(option, materialNameCounts[option.file_name || '未命名材料'])}</option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-0 top-0.5 h-3.5 w-3.5 text-[#8B7355]" />
            </label>
          ) : (
            <p className="truncate text-xs font-semibold text-[#1A1A1A]" title={material.file_name}>{material.file_name}</p>
          )}
          <p className="text-[11px] text-[#9CA3AF]">
            {standalone ? '从第 1 页开始预览' : precise && hasPage ? `定位到第 ${page} 页` : hasPage ? `页码线索：第 ${page} 页` : '暂无精确页码'}
          </p>
          {!standalone && hasPage && !anchorRects.length && <p className="text-[11px] text-[#9CA3AF]">如页面未显示原文框选，请在本页人工核对。</p>}
        </div>
        <a
          href={bsMaterialFileUrl(material.id)}
          download={material.file_name}
          aria-label="下载材料"
          title="下载材料"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#6B7280] hover:bg-[#FAFAF7]"
        >
          <Download className="h-3.5 w-3.5" />
        </a>
        {onClose && (
          <button
            type="button"
            aria-label="关闭预览"
            title="关闭预览"
            onClick={onClose}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#6B7280] hover:bg-[#FAFAF7]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </header>
      {!standalone && (locationMode === 'unresolved' || (!precise && !hasPage)) ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] text-[#6B7280]">
          暂未定位到具体页，请在原件中人工核对该材料。
        </div>
      ) : (
        <div className="min-h-0 flex-1">
          <StructuralPdfPreview
            key={material.id}
            src={bsMaterialFileUrl(material.id)}
            fileName={material.file_name}
            initialPage={hasPage ? Number(page) : 1}
            activeFinding={activeFinding}
            onPageChange={(nextPage) => setViewPage({ materialId: material.id, sourcePage: page, visiblePage: nextPage })}
            showFileName={false}
            fitWidth={fitWidth}
            onError={onError}
          />
        </div>
      )}
      {!standalone && hasPage && locationMode !== 'unresolved' && (
        <PageTextPanel key={`${material.id}:${visiblePage}`} materialId={material.id} page={visiblePage}
          refs={matchingReferences(finding, material.id ?? material.material_id, visiblePage)} />
      )}
    </section>
  )
}
