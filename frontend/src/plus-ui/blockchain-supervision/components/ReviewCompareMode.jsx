import { useCallback, useEffect, useRef, useState } from 'react'

// 刀D（设计 104 §4.3）：全屏证据对照模式。
// - 左“问题列”（复用 ReviewQueue，列表+选中问题摘要/规则依据）与右“原文列”并排占满视口；
// - 分隔条可鼠标拖拽、键盘 ←/→ 调整（320–720px，默认 440px），并把宽度写进 aria-valuenow；
// - Esc 关闭、关闭后焦点回到入口按钮由父组件负责；这里只管布局与拖拽。
// - 纯展示组件：不取数、不写库、不改变选择态，左右两栏都由调用方传入。

const MIN_LEFT_WIDTH = 320
const MAX_LEFT_WIDTH = 960
const DEFAULT_LEFT_WIDTH = 440
const MIN_RIGHT_WIDTH = 360
const KEYBOARD_STEP = 24

function clampLeftWidth(value, containerWidth) {
  // 容器宽度未知（首帧/jsdom 测试环境）时不按容器夹紧，仍受 320–720 约束。
  const upperByContainer = containerWidth > 0
    ? Math.max(MIN_LEFT_WIDTH, containerWidth - MIN_RIGHT_WIDTH)
    : MAX_LEFT_WIDTH
  return Math.min(Math.max(value, MIN_LEFT_WIDTH), Math.min(MAX_LEFT_WIDTH, upperByContainer))
}

export default function ReviewCompareMode({
  title = '证据对照',
  subtitle = null,
  left = null,
  right = null,
  onClose,
  closeButtonRef,
  prototypeMode = false,
  headerActions = null,
}) {
  const containerRef = useRef(null)
  const dragRef = useRef(null)
  const expandedWidthRef = useRef(DEFAULT_LEFT_WIDTH)
  const [leftWidth, setLeftWidth] = useState(() => {
    if (!prototypeMode) return DEFAULT_LEFT_WIDTH
    if (typeof window === 'undefined' || !window.innerWidth) return 560
    return Math.min(MAX_LEFT_WIDTH, Math.max(560, Math.round(window.innerWidth * 0.5)))
  })
  const [leftCollapsed, setLeftCollapsed] = useState(false)

  const applyWidth = useCallback((next) => {
    const containerWidth = containerRef.current?.clientWidth || 0
    const width = clampLeftWidth(next, containerWidth)
    expandedWidthRef.current = width
    setLeftWidth(width)
    setLeftCollapsed(false)
  }, [])

  const toggleLeft = useCallback(() => {
    setLeftCollapsed((collapsed) => {
      if (collapsed) {
        const width = clampLeftWidth(expandedWidthRef.current, containerRef.current?.clientWidth || 0)
        setLeftWidth(width)
        return false
      }
      expandedWidthRef.current = leftWidth
      return true
    })
  }, [leftWidth])

  useEffect(() => {
    // 视口尺寸变化时重新夹紧，避免左栏在窄屏把原文列挤没。
    const onResize = () => setLeftWidth((current) => (
      clampLeftWidth(current, containerRef.current?.clientWidth || 0)
    ))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const endDrag = () => {
    dragRef.current = null
  }

  const onPointerDown = (event) => {
    if (event.button != null && event.button !== 0) return
    event.preventDefault()
    dragRef.current = { startX: event.clientX, startWidth: leftWidth }
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId)
    } catch {
      // jsdom / 老浏览器没有 pointer capture：退化为普通 pointermove 监听。
    }
  }

  const onPointerMove = (event) => {
    if (!dragRef.current) return
    applyWidth(dragRef.current.startWidth + (event.clientX - dragRef.current.startX))
  }

  const onKeyDown = (event) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      applyWidth(leftWidth - KEYBOARD_STEP)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      applyWidth(leftWidth + KEYBOARD_STEP)
    } else if (event.key === 'Home') {
      event.preventDefault()
      applyWidth(MIN_LEFT_WIDTH)
    } else if (event.key === 'End') {
      event.preventDefault()
      applyWidth(MAX_LEFT_WIDTH)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      toggleLeft()
    }
  }

  const dialog = (
    <div
      ref={containerRef}
      data-testid="review-compare-mode"
      role="dialog"
      aria-modal="true"
      aria-label="证据对照"
      className={prototypeMode
        ? 'flex h-full min-h-0 min-w-0 w-full flex-col overflow-hidden bg-[#F4F7FA]'
        : 'fixed inset-0 z-[70] flex min-h-0 min-w-0 flex-col bg-white'}
    >
      {prototypeMode && <h2 className="sr-only">全屏证据对照</h2>}
      {!prototypeMode && <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[#EFE6D8] bg-[#FDFAF4] px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 aria-label={prototypeMode ? '全屏证据对照' : title} className="text-sm font-semibold text-[#1A1A1A]">{title}</h2>
          <p className="truncate text-[11px] text-[#6B7280]">
            {subtitle || '左侧问题与规则依据，右侧原文；拖动分隔条调整宽度，Esc 退出'}
          </p>
        </div>
        {headerActions}
        <span className="hidden shrink-0 text-[11px] text-[#9CA3AF] sm:inline">{prototypeMode ? 'Esc 关闭' : 'Esc 退出对照'}</span>
        <button
          type="button"
          ref={closeButtonRef}
          data-testid="compare-close"
          onClick={() => onClose?.()}
          className="min-h-9 shrink-0 rounded-[5px] border border-[#8B7355] bg-white px-3 text-xs text-[#8B7355] hover:bg-[#F7F1E9]"
        >
          {prototypeMode ? '关闭' : '退出对照'}
        </button>
      </header>}
      <div className="flex min-h-0 min-w-0 flex-1">
        <div
          data-testid="compare-left"
          id="compare-left-pane"
          aria-hidden={leftCollapsed || undefined}
          hidden={leftCollapsed}
          className="flex min-h-0 min-w-0 shrink-0 flex-col overflow-hidden border-r border-[#EFE6D8]"
          style={{ width: `${leftWidth}px` }}
        >
          {left}
        </div>
        <div
          data-testid="compare-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整问题列宽度"
          aria-controls="compare-left-pane"
          aria-expanded={!leftCollapsed}
          aria-valuenow={leftWidth}
          aria-valuemin={MIN_LEFT_WIDTH}
          aria-valuemax={MAX_LEFT_WIDTH}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyDown={onKeyDown}
          onDoubleClick={toggleLeft}
          className="w-2 shrink-0 cursor-col-resize touch-none bg-[#F3EDE3] hover:bg-[#C8B89E] focus:outline-none focus:ring-2 focus:ring-[#8B7355]"
        />
        <div data-testid="compare-right" className="flex min-h-0 min-w-0 flex-1 flex-col bg-[#EEF1F5]">
          {right}
        </div>
      </div>
    </div>
  )

  return prototypeMode
    ? <div className="relative flex h-full min-h-0 min-w-0 w-full flex-1 items-stretch justify-stretch overflow-hidden bg-[#F4F7FA]">{dialog}</div>
    : dialog
}
