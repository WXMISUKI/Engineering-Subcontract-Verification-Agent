import React, { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
// P4-1：pdfjs modern build 的 worker realm 依赖 ES2025 新 API（upsert；Chrome 145+），
// TRAE 内嵌浏览器（Electron 内核）缺失会导致渲染崩溃；改用 legacy build（核心 js 自 polyfill，API 一致）。
// 兼容补丁仍由 main.jsx 单一入口注入，此处不重复补丁。
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs'

// T03：纯几何/缩放/坐标换算逻辑已抽到 ../lib/pdfGeometry.js（纯函数、零依赖、零 DOM）。
import {
  MAX_SCALE,
  MIN_SCALE,
  SCALE_STEP,
  buildFindingFocusKey,
  buildPageWindow,
  clampPage,
  clampScale,
  deriveFindingRectsFromTextAnchors,
  mergeRects,
  normalizeAnchorText,
  normalizeRectList,
  pickDominantVisiblePage,
  toDisplayRect,
} from '../lib/pdfGeometry'
import { scaledPageScrollTop, scrollSlotWithinContainer } from '../lib/pdfScroll'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/legacy/build/pdf.worker.min.mjs',
  import.meta.url,
).href

const RENDER_BUFFER = 1
const JUMP_BUFFER = 2
const V_THRESH = 25.0
const SETTLE_MS = 160
const STALL_MS = 500
const FINDING_RETRY_MS = 80
const LINE_GROUP_TOLERANCE = 4

// extractPageTextAnchors 直接依赖 pdfjsLib.Util.transform（库级矩阵变换），
// 为保持 lib/pdfGeometry.js「零依赖」契约，此函数保留在组件内；
// 其内部调用的 mergeRects/normalizeAnchorText 已从 pdfGeometry 引入。
// 坐标系契约（2026-10-06 修复）：入参 viewport 由 page.getViewport({ scale }) 构建，
// **已含当前缩放**，故 Util.transform 的结果落在「视口像素」坐标系；而本组件与后端的
// rects 契约统一为 **PDF 点**（后端来自 PyMuPDF get_text('words')，见
// plus/safety_review/extract.py），最终由 lib/pdfGeometry.toDisplayRect(rect, scale)
// 统一乘 scale 还原为显示像素。这里必须把视口像素除回 scale，否则「无后端 rects 的
// 兜底定位」会被二次放大：x/y 偏移、宽度偏大；而高度曾因用的是未缩放的 item.height、
// 再乘一次 scale 而碰巧正确，掩盖了该缺陷。
function extractPageTextAnchors(textContent, viewport, page) {
  const items = Array.isArray(textContent?.items) ? textContent.items : []
  const viewportScale = Number(viewport?.scale) || 1
  const glyphs = items
    .map((item) => {
      const text = String(item?.str || '')
      if (!text.trim()) return null
      const transform = pdfjsLib.Util.transform(viewport.transform, item.transform)
      const x0 = (Number(transform[4]) || 0) / viewportScale
      const height = Math.abs(Number(item?.height) || (Number(transform[3]) || 0) / viewportScale || 0)
      const width = Math.abs(Number(item?.width) || 0)
      const y1 = (Number(transform[5]) || 0) / viewportScale
      const y0 = y1 - height
      if (width <= 0 || height <= 0) return null
      return {
        text,
        x0,
        y0,
        x1: x0 + width,
        y1,
      }
    })
    .filter(Boolean)
    .sort((a, b) => {
      const yDiff = a.y0 - b.y0
      if (Math.abs(yDiff) > LINE_GROUP_TOLERANCE) return yDiff
      return a.x0 - b.x0
    })

  const lines = []
  for (const glyph of glyphs) {
    const current = lines[lines.length - 1]
    if (!current || Math.abs(current.y0 - glyph.y0) > LINE_GROUP_TOLERANCE) {
      lines.push({
        y0: glyph.y0,
        glyphs: [glyph],
      })
      continue
    }
    current.glyphs.push(glyph)
    current.y0 = Math.min(current.y0, glyph.y0)
  }

  return lines.map((line, index) => {
    const sortedGlyphs = [...line.glyphs].sort((a, b) => a.x0 - b.x0)
    const rect = mergeRects(sortedGlyphs)
    const lineNumber = index + 1
    return {
      lineNumber,
      lineId: `P${page}-L${String(lineNumber).padStart(3, '0')}`,
      text: sortedGlyphs.map((glyph) => glyph.text).join(''),
      normalizedText: normalizeAnchorText(sortedGlyphs.map((glyph) => glyph.text).join('')),
      rect,
    }
  }).filter((line) => line.rect)
}

const StructuralPdfPreview = forwardRef(function StructuralPdfPreview(
  { src, fileName = '计算书.pdf', activeFinding = null, initialPage = null, onError = null, onPageChange = null, showFileName = true, fitWidth = false },
  ref,
) {
  const scrollRef = useRef(null)
  const slotRefs = useRef([])
  const canvasRefs = useRef(new Map())
  const renderTasksRef = useRef(new Map())
  const pageTextAnchorsRef = useRef(new Map())
  const drawnRef = useRef(new Set())
  const objectUrlRef = useRef('')
  const visiblePageRef = useRef(1)
  const onPageChangeRef = useRef(onPageChange)
  onPageChangeRef.current = onPageChange
  const pendingWindowRef = useRef(new Set())
  const fastRef = useRef(false)
  const settleTimerRef = useRef(null)
  const stallTimerRef = useRef(null)
  const findingRetryTimerRef = useRef(null)
  const findingRetryFrameRef = useRef(null)
  const pendingFindingRef = useRef(null)
  const appliedFindingKeyRef = useRef('')
  const scalePositionRef = useRef(null)

  const [fileUrl, setFileUrl] = useState('')
  const [pdfDoc, setPdfDoc] = useState(null)
  const [pageCount, setPageCount] = useState(0)
  const [pageSizes, setPageSizes] = useState([])
  const [renderedPages, setRenderedPages] = useState(() => new Set())
  const [pageNumber, setPageNumber] = useState(1)
  const [pageInput, setPageInput] = useState('1')
  const [scale, setScale] = useState(1.1)
  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const setScalePreservingPosition = (change) => {
    const previousScale = scaleRef.current
    const next = typeof change === 'function' ? change(previousScale) : change
    if (next === previousScale) return
    const root = scrollRef.current
    const page = visiblePageRef.current
    const slot = slotRefs.current[page - 1]
    if (root && slot && !scalePositionRef.current) {
      scalePositionRef.current = { page, scale: previousScale, offset: root.scrollTop - slot.offsetTop }
    }
    setScale(next)
  }
  const [loading, setLoading] = useState(true)
  const [rendering, setRendering] = useState(false)
  const [error, setError] = useState('')
  const [textAnchorVersion, setTextAnchorVersion] = useState(0)

  const displayScale = useMemo(() => Math.round(scale * 100), [scale])
  const activeFindingPage = useMemo(() => {
    const page = Number(activeFinding?.page)
    return Number.isFinite(page) && page > 0 ? Math.floor(page) : 0
  }, [activeFinding])
  const activeFindingRects = useMemo(
    () => normalizeRectList(activeFinding?.rects),
    [activeFinding],
  )
  const activeDerivedRects = useMemo(() => {
    if (activeFindingRects.length > 0) return []
    const pageAnchors = pageTextAnchorsRef.current.get(activeFindingPage) || []
    return normalizeRectList(deriveFindingRectsFromTextAnchors(activeFinding, pageAnchors))
  }, [activeFinding, activeFindingPage, activeFindingRects, textAnchorVersion])
  const effectiveActiveFindingRects = useMemo(
    () => (activeFindingRects.length > 0 ? activeFindingRects : activeDerivedRects),
    [activeDerivedRects, activeFindingRects],
  )
  const activeFindingKey = useMemo(
    () => buildFindingFocusKey(activeFinding),
    [activeFinding],
  )

  useEffect(() => {
    setPageInput(String(pageNumber || 1))
  }, [pageNumber])

  useEffect(() => {
    let cancelled = false

    async function loadBlobUrl() {
      setLoading(true)
      setRendering(false)
      setError('')
      setFileUrl('')
      setPdfDoc(null)
      setPageCount(0)
      setPageNumber(1)
      setPageSizes([])
      setRenderedPages(new Set())
      pendingWindowRef.current = new Set()
      scalePositionRef.current = null
      fastRef.current = false
      drawnRef.current = new Set()
      pageTextAnchorsRef.current = new Map()
      setTextAnchorVersion((value) => value + 1)
      visiblePageRef.current = 1

      renderTasksRef.current.forEach((task) => {
        try {
          task.cancel()
        } catch {
          // ignore
        }
      })
      renderTasksRef.current.clear()

      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current)
        objectUrlRef.current = ''
      }

      try {
        const response = await fetch(src, { credentials: 'include' })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const blob = await response.blob()
        if (cancelled) return
        const objectUrl = URL.createObjectURL(blob)
        objectUrlRef.current = objectUrl
        setFileUrl(objectUrl)
      } catch (err) {
        if (!cancelled) {
          const msg = `PDF 预览失败：${err?.message || String(err)}`
          setError(msg)
          onError?.(msg)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadBlobUrl()

    return () => {
      cancelled = true
      renderTasksRef.current.forEach((task) => {
        try {
          task.cancel()
        } catch {
          // ignore
        }
      })
      renderTasksRef.current.clear()
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current)
        objectUrlRef.current = ''
      }
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      if (stallTimerRef.current) {
        clearTimeout(stallTimerRef.current)
        stallTimerRef.current = null
      }
      if (findingRetryTimerRef.current) {
        clearTimeout(findingRetryTimerRef.current)
        findingRetryTimerRef.current = null
      }
      if (findingRetryFrameRef.current) {
        cancelAnimationFrame(findingRetryFrameRef.current)
        findingRetryFrameRef.current = null
      }
    }
  }, [src])

  const clearFindingRetry = () => {
    if (findingRetryTimerRef.current) {
      clearTimeout(findingRetryTimerRef.current)
      findingRetryTimerRef.current = null
    }
    if (findingRetryFrameRef.current) {
      cancelAnimationFrame(findingRetryFrameRef.current)
      findingRetryFrameRef.current = null
    }
  }

  useEffect(() => {
    if (!fileUrl) return
    let cancelled = false

    async function loadPdf() {
      try {
        const base = import.meta.env.BASE_URL
        const doc = await pdfjsLib.getDocument({
          url: fileUrl,
          wasmUrl: `${base}pdfjs/wasm/`,
          cMapUrl: `${base}pdfjs/cmaps/`,
          cMapPacked: true,
          standardFontDataUrl: `${base}pdfjs/standard_fonts/`,
        }).promise
        if (!cancelled) {
          setPdfDoc(doc)
          setPageCount(doc.numPages)
        }
      } catch (err) {
        if (!cancelled) {
          const msg = `PDF 加载失败：${err?.message || String(err)}`
          setError(msg)
          onError?.(msg)
        }
      }
    }

    loadPdf()
    return () => {
      cancelled = true
    }
  }, [fileUrl])

  useEffect(() => {
    if (!pdfDoc) return
    let cancelled = false

    async function collectSizes() {
      const nextSizes = []
      for (let index = 1; index <= pdfDoc.numPages; index += 1) {
        if (cancelled) return
        const page = await pdfDoc.getPage(index)
        const viewport = page.getViewport({ scale })
        nextSizes.push({ width: viewport.width, height: viewport.height })
      }
      if (cancelled) return
      drawnRef.current = new Set()
      pendingWindowRef.current = new Set()
      fastRef.current = false
      setRenderedPages(new Set())
      setPageSizes(nextSizes)
      setPageCount(pdfDoc.numPages)
    }

    collectSizes()
    return () => {
      cancelled = true
    }
  }, [pdfDoc, scale])

  // 刀D（设计 104 §4.3）：fit-width——按容器宽度求缩放，避免固定 110% 在宽栏里只剩大片灰边。
  // 默认 false，结构核查模块行为不变；容器尺寸变化时重算（用户手动放大后仍可按窗口宽度复位）。
  useEffect(() => {
    if (!fitWidth || !pdfDoc || !pageCount) return undefined
    let cancelled = false

    async function fitToContainer() {
      const root = scrollRef.current
      if (!root) return
      try {
        const page = await pdfDoc.getPage(1)
        if (cancelled) return
        const baseWidth = page.getViewport({ scale: 1 }).width || 0
        if (!baseWidth) return
        const available = Math.max(root.clientWidth - 32, 240)
        const next = clampScale(available / baseWidth)
        setScalePreservingPosition((current) => (Math.abs(current - next) < 0.005 ? current : next))
      } catch {
        // 尺寸换算失败不改变现有缩放：宁可保持当前比例，也不把页面缩没。
      }
    }

    fitToContainer()
    let observer = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => { fitToContainer() })
      if (scrollRef.current) observer.observe(scrollRef.current)
    } else {
      window.addEventListener('resize', fitToContainer)
    }
    return () => {
      cancelled = true
      if (observer) observer.disconnect()
      window.removeEventListener('resize', fitToContainer)
    }
  }, [fitWidth, pdfDoc, pageCount])

  useEffect(() => {
    if (!pageSizes.length) return undefined
    const root = scrollRef.current
    if (!root) return undefined

    const ratios = new Map()
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const page = Number(entry.target.dataset.page)
          ratios.set(page, entry.intersectionRatio)
        }

        const visiblePages = [...ratios.entries()]
          .filter(([, ratio]) => ratio > 0)
          .map(([page]) => page)

        if (!visiblePages.length) return

        // 挑出占屏比例最大的可见页（并列取先出现者），逻辑见 lib/pdfGeometry.pickDominantVisiblePage。
        const bestPage = pickDominantVisiblePage(ratios)

        if (bestPage !== visiblePageRef.current) {
          visiblePageRef.current = bestPage
          setPageNumber(bestPage)
          onPageChangeRef.current?.(bestPage)
        }

        const pending = buildPageWindow(bestPage, RENDER_BUFFER, pageSizes.length)
        pendingWindowRef.current = pending
        if (!fastRef.current) {
          setRenderedPages(new Set(pending))
        }
      },
      { root, threshold: [0, 0.25, 0.5, 0.75, 1] },
    )

    slotRefs.current.forEach((slot) => {
      if (slot) observer.observe(slot)
    })

    return () => observer.disconnect()
  }, [pageSizes])

  useEffect(() => {
    if (!pageSizes.length) return undefined
    const root = scrollRef.current
    if (!root) return undefined

    let velocityState = { y: root.scrollTop, t: performance.now() }

    const onScroll = () => {
      const now = performance.now()
      const y = root.scrollTop
      const dt = Math.max(1, now - velocityState.t)
      const velocity = Math.abs(y - velocityState.y) / dt
      velocityState = { y, t: now }

      const isFast = velocity >= V_THRESH
      if (isFast && !fastRef.current) {
        fastRef.current = true
        setRenderedPages(new Set())
      }

      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current)
      }
      settleTimerRef.current = setTimeout(() => {
        settleTimerRef.current = null
        if (stallTimerRef.current) {
          clearTimeout(stallTimerRef.current)
          stallTimerRef.current = null
        }
        fastRef.current = false
        setRenderedPages(new Set(pendingWindowRef.current))
      }, SETTLE_MS)

      if (!stallTimerRef.current) {
        stallTimerRef.current = setTimeout(() => {
          stallTimerRef.current = null
          setRenderedPages(new Set(pendingWindowRef.current))
        }, STALL_MS)
      }
    }

    root.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      root.removeEventListener('scroll', onScroll)
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      if (stallTimerRef.current) {
        clearTimeout(stallTimerRef.current)
        stallTimerRef.current = null
      }
    }
  }, [pageSizes])

  useEffect(() => {
    if (!pdfDoc || !pageSizes.length) return undefined
    let cancelled = false

    for (const page of [...drawnRef.current]) {
      if (!renderedPages.has(page)) {
        drawnRef.current.delete(page)
        const task = renderTasksRef.current.get(page)
        if (task) {
          try {
            task.cancel()
          } catch {
            // ignore
          }
          renderTasksRef.current.delete(page)
        }
      }
    }

    async function renderPages() {
      const pagesToRender = [...renderedPages]
        .filter((page) => page >= 1 && page <= pageSizes.length)
        .sort((a, b) => a - b)

      if (!pagesToRender.length) {
        setRendering(false)
        return
      }

      setRendering(true)
      try {
        for (const pageNumberToRender of pagesToRender) {
          if (cancelled) return
          if (drawnRef.current.has(pageNumberToRender)) continue
          const canvas = canvasRefs.current.get(pageNumberToRender)
          if (!canvas) continue

          const previousTask = renderTasksRef.current.get(pageNumberToRender)
          if (previousTask) {
            try {
              previousTask.cancel()
            } catch {
              // ignore
            }
            renderTasksRef.current.delete(pageNumberToRender)
          }

          const page = await pdfDoc.getPage(pageNumberToRender)
          if (cancelled) return
          const viewport = page.getViewport({ scale })
          const textContent = await page.getTextContent()
          if (cancelled) return
          pageTextAnchorsRef.current.set(
            pageNumberToRender,
            extractPageTextAnchors(textContent, viewport, pageNumberToRender),
          )
          setTextAnchorVersion((value) => value + 1)
          const context = canvas.getContext('2d')
          if (!context) continue

          const ratio = window.devicePixelRatio || 1
          canvas.width = Math.floor(viewport.width * ratio)
          canvas.height = Math.floor(viewport.height * ratio)
          canvas.style.width = `${viewport.width}px`
          canvas.style.height = `${viewport.height}px`
          context.setTransform(ratio, 0, 0, ratio, 0, 0)
          context.clearRect(0, 0, viewport.width, viewport.height)

          const task = page.render({ canvasContext: context, viewport })
          renderTasksRef.current.set(pageNumberToRender, task)

          try {
            await task.promise
            drawnRef.current.add(pageNumberToRender)
          } catch (err) {
            if (!/cancelled/i.test(String(err))) {
              throw err
            }
          } finally {
            const currentTask = renderTasksRef.current.get(pageNumberToRender)
            if (currentTask === task) {
              renderTasksRef.current.delete(pageNumberToRender)
            }
          }
        }
      } catch (err) {
        if (!cancelled) {
          const msg = `PDF 页面渲染失败：${err?.message || String(err)}`
          setError(msg)
          onError?.(msg)
        }
      } finally {
        if (!cancelled) setRendering(false)
      }
    }

    renderPages()
    return () => {
      cancelled = true
    }
  }, [pdfDoc, renderedPages, scale, pageSizes])

  const canUseControls = Boolean(pdfDoc && pageCount && !loading)

  const applyJumpWindow = (targetPage) => {
    const page = clampPage(targetPage, pageSizes.length || pageCount || 1)
    const jumpWindow = buildPageWindow(page, JUMP_BUFFER, pageSizes.length || pageCount || 1)
    pendingWindowRef.current = jumpWindow
    fastRef.current = false
    setRenderedPages(new Set(jumpWindow))
    return page
  }

  // 只滚动 PDF 内部容器，避免浏览器把外层工作台一并滚走。
  const scrollSlotToTop = (slot) => {
    return scrollSlotWithinContainer(scrollRef.current, slot)
  }

  const queueFindingRetry = () => {
    clearFindingRetry()
    findingRetryFrameRef.current = requestAnimationFrame(() => {
      findingRetryFrameRef.current = null
      findingRetryTimerRef.current = setTimeout(() => {
        findingRetryTimerRef.current = null
        if (pendingFindingRef.current) {
          scrollToFindingInternal(pendingFindingRef.current, { allowRetry: false })
        }
      }, FINDING_RETRY_MS)
    })
  }

  const scrollToPageInternal = (targetPage) => {
    pendingFindingRef.current = null
    // 手动翻页不应让当前证据的自动定位再次触发并跳回原页。
    clearFindingRetry()
    const page = applyJumpWindow(targetPage)
    const slot = slotRefs.current[page - 1]
    if (slot) {
      scrollSlotToTop(slot)
      visiblePageRef.current = page
      setPageNumber(page)
      onPageChangeRef.current?.(page)
    }
  }

  const scrollToFindingInternal = (finding, options = {}) => {
    const { allowRetry = true } = options
    if (!finding) return false
    const page = applyJumpWindow(finding?.page)
    const slot = slotRefs.current[page - 1]
    const findingKey = buildFindingFocusKey(finding)
    pendingFindingRef.current = finding
    if (!slot) {
      if (allowRetry) queueFindingRetry()
      return false
    }

    const root = scrollRef.current
    const resolvedRects = normalizeRectList(finding?.rects).length > 0
      ? normalizeRectList(finding?.rects)
      : normalizeRectList(deriveFindingRectsFromTextAnchors(finding, pageTextAnchorsRef.current.get(page) || []))
    const firstRect = resolvedRects[0]
    visiblePageRef.current = page
    setPageNumber(page)
    onPageChangeRef.current?.(page)

    if (root && firstRect) {
      const targetTop = Math.max(
        slot.offsetTop + (firstRect.y0 * scale) - Math.max(root.clientHeight * 0.28, 80),
        0,
      )
      root.scrollTo({ top: targetTop, behavior: 'auto' })
      appliedFindingKeyRef.current = findingKey
      clearFindingRetry()
      return true
    }

    scrollSlotToTop(slot)
    appliedFindingKeyRef.current = findingKey
    clearFindingRetry()
    return true
  }

  // 页面尺寸由 PDF.js 异步计算，待新尺寸就绪后恢复缩放前的页内位置。
  useLayoutEffect(() => {
    const previous = scalePositionRef.current
    const root = scrollRef.current
    const slot = previous && slotRefs.current[previous.page - 1]
    if (root && slot && previous.scale !== scale) {
      const top = scaledPageScrollTop(slot.offsetTop, previous.offset, previous.scale, scale)
      if (top != null) {
        root.scrollTo({ top, behavior: 'auto' })
        visiblePageRef.current = previous.page
        setPageNumber(previous.page)
      }
    }
    scalePositionRef.current = null
  }, [pageSizes])

  useEffect(() => {
    if (!activeFindingPage || !pageSizes.length) return
    const targetKey = buildFindingFocusKey(activeFinding)
    if (!targetKey) return
    pendingFindingRef.current = activeFinding
    if (appliedFindingKeyRef.current === targetKey) return
    scrollToFindingInternal(activeFinding)
  }, [activeFinding, activeFindingKey, activeFindingPage, pageSizes, scale])

  // 2026-09-20：initialPage——核查范围弹窗「页详情」打开时直接落到目标页。
  // pageSizes 就绪后才可跳（slotRefs 依赖尺寸）；同一目标只跳一次，
  // 用户随后手动翻页/缩放不被打断（缩放触发 pageSizes 重算时 key 相同不重跳）。
  const lastInitialJumpRef = useRef('')
  useEffect(() => {
    if (!pageSizes.length || !initialPage || Number(initialPage) <= 1) return
    const key = String(initialPage)
    if (lastInitialJumpRef.current === key) return
    lastInitialJumpRef.current = key
    // 精确定位与初始页相同时，前面的定位 effect 已滚到证据；不能再跳回页首。
    if (activeFindingPage === Number(initialPage)) return
    scrollToPageInternal(Number(initialPage))
  }, [pageSizes, initialPage, activeFindingPage])

  useEffect(() => () => {
    clearFindingRetry()
  }, [])

  const commitPageInput = () => {
    const next = clampPage(pageInput, pageCount || pageSizes.length || 1)
    setPageInput(String(next))
    scrollToPageInternal(next)
  }

  useImperativeHandle(ref, () => ({
    scrollToPage(targetPage) {
      scrollToPageInternal(targetPage)
    },
    scrollToFinding(finding) {
      pendingFindingRef.current = finding
      scrollToFindingInternal(finding)
    },
  }), [pageCount, pageSizes.length, scale])

  if (error) {
    return (
      <div className="flex h-full min-h-0 flex-col bg-[#EEF1F5]">
        <div className="flex min-h-[42px] items-center justify-between gap-2 border-b border-[#D8DEE8] bg-white px-3">
          <div className="min-w-0">
            {showFileName && <div className="truncate text-[12px] font-semibold text-[#1F2937]">{fileName}</div>}
            <div className="text-[11px] text-[#6B7280]">PDF 预览</div>
          </div>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-[#525659] p-4">
          <div className="max-w-md rounded-[6px] border border-[#F3B8B8] bg-white p-4 text-center shadow-sm">
            <RefreshCw className="mx-auto mb-2 h-5 w-5 text-[#B91C1C]" />
            <p className="text-[13px] font-semibold text-[#991B1B]">{error}</p>
            <p className="mt-1 text-[12px] leading-5 text-[#6B7280]">
              你仍然可以打开或下载原始 PDF 继续查看。
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#EEF1F5]">
      <div className="flex min-h-[42px] items-center justify-between gap-2 border-b border-[#D8DEE8] bg-white px-3">
        <div className="min-w-0">
          {showFileName && <div className="truncate text-[12px] font-semibold text-[#1F2937]">{fileName}</div>}
          <div className="text-[11px] text-[#6B7280]">
            {pageCount ? `第 ${pageNumber} / ${pageCount} 页 · ${displayScale}%` : 'PDF.js 预览'}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label="上一页"
            title="上一页"
            disabled={!canUseControls || pageNumber <= 1}
            onClick={() => scrollToPageInternal(pageNumber - 1)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <input
            aria-label="页码"
            disabled={!canUseControls}
            value={pageInput}
            onChange={(event) => setPageInput(event.target.value.replace(/[^\d]/g, '').slice(0, 6))}
            onBlur={commitPageInput}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commitPageInput()
              }
            }}
            className="h-8 w-14 rounded-[5px] border border-[#E5E7EB] bg-white text-center text-[12px] font-medium text-[#1F2937] outline-none focus:border-[#7C2D12] disabled:opacity-40"
          />
          <button
            type="button"
            aria-label="下一页"
            title="下一页"
            disabled={!canUseControls || pageNumber >= pageCount}
            onClick={() => scrollToPageInternal(pageNumber + 1)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>

          <span className="mx-1 h-5 w-px bg-[#E5E7EB]" />

          <button
            type="button"
            aria-label="缩小"
            title="缩小"
            disabled={!canUseControls || scale <= MIN_SCALE}
            onClick={() => setScalePreservingPosition((value) => clampScale(value - SCALE_STEP))}
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <button
            type="button"
            disabled={!canUseControls}
            onClick={() => setScalePreservingPosition(1.1)}
            className="h-8 min-w-[52px] rounded-[5px] border border-[#E5E7EB] px-2 text-[12px] font-medium text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {displayScale}%
          </button>
          <button
            type="button"
            aria-label="放大"
            title="放大"
            disabled={!canUseControls || scale >= MAX_SCALE}
            onClick={() => setScalePreservingPosition((value) => clampScale(value + SCALE_STEP))}
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomIn className="h-4 w-4" />
          </button>

          <span className="mx-1 h-5 w-px bg-[#E5E7EB]" />

          <button
            type="button"
            disabled={!canUseControls}
            onClick={() => setScalePreservingPosition(1.1)}
            className="h-8 min-w-[52px] rounded-[5px] border border-[#E5E7EB] px-2 text-[12px] font-medium text-[#4B5563] hover:bg-[#FAFAF7] disabled:cursor-not-allowed disabled:opacity-40"
          >
            重置
          </button>

          <a
            href={src}
            target="_blank"
            rel="noreferrer"
            aria-label="新标签打开"
            title="新标签打开"
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7]"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
          <a
            href={src}
            download={fileName}
            aria-label="下载 PDF"
            title="下载 PDF"
            className="inline-flex h-8 w-8 items-center justify-center rounded-[5px] border border-[#E5E7EB] text-[#4B5563] hover:bg-[#FAFAF7]"
          >
            <Download className="h-4 w-4" />
          </a>
        </div>
      </div>

      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto bg-[#525659] p-4">
        {(loading || rendering) && (
          <div className="pointer-events-none absolute inset-x-0 top-4 z-10 mx-auto flex w-fit items-center rounded-[6px] bg-[#111827]/85 px-3 py-2 text-[12px] text-white shadow-sm">
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            {loading ? '正在加载计算书 PDF...' : '正在渲染当前页...'}
          </div>
        )}

        <div className="flex min-h-full flex-col items-center gap-3">
          {pageSizes.map((size, index) => {
            const page = index + 1
            const isRendered = renderedPages.has(page)
            return (
              <div
                key={page}
                ref={(el) => {
                  slotRefs.current[index] = el
                }}
                data-page={page}
                className="relative flex w-full justify-center"
                style={{ minHeight: `${size.height}px` }}
              >
                <div
                  className="relative"
                  style={{ width: `${size.width}px`, minHeight: `${size.height}px` }}
                >
                  <div className="absolute -top-7 left-0 rounded-[4px] bg-black/65 px-2 py-0.5 text-[10px] text-white">
                    第 {page} 页
                  </div>

                  {isRendered ? (
                    <canvas
                      ref={(el) => {
                        if (el) canvasRefs.current.set(page, el)
                        else canvasRefs.current.delete(page)
                      }}
                      className="block max-w-none rounded-[2px] bg-white shadow-md"
                    />
                  ) : (
                    <div
                      className="flex items-center justify-center rounded-[2px] border border-white/15 bg-white shadow-md"
                      style={{ width: `${size.width}px`, height: `${size.height}px` }}
                    >
                      <span className="text-[12px] text-[#9CA3AF]">页码 {page}</span>
                    </div>
                  )}

                  {activeFindingPage === page && effectiveActiveFindingRects.length > 0 && (
                    <div className="pointer-events-none absolute inset-0 z-[2]" aria-label="finding-highlight-overlay">
                      {effectiveActiveFindingRects.map((rect, rectIndex) => {
                        // PDF 坐标 → canvas 显示盒（换算逻辑见 lib/pdfGeometry.toDisplayRect）。
                        const box = toDisplayRect(rect, scale)
                        return (
                          <div
                            key={`${page}-${activeFinding?.focusKey || 'active'}-${rectIndex}`}
                            className="absolute rounded-[2px] border border-[#F97316] bg-[#FDBA74]/30 shadow-[0_0_0_1px_rgba(249,115,22,0.25)]"
                            style={{
                              left: `${box.left}px`,
                              top: `${box.top}px`,
                              width: `${box.width}px`,
                              height: `${box.height}px`,
                            }}
                          />
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
})

export default StructuralPdfPreview
