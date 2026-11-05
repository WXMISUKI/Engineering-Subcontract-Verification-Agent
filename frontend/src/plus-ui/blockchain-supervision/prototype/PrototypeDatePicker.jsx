import { useEffect, useMemo, useRef, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'

function parseIso(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function isoOf(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}

function displayOf(value) {
  const date = parseIso(value)
  return date ? `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}` : ''
}

export default function PrototypeDatePicker({ value = '', onChange, disabled = false, readOnly = false }) {
  const selected = parseIso(value)
  const today = new Date()
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(() => selected || today)
  const rootRef = useRef(null)

  useEffect(() => {
    if (selected) setCursor(selected)
  }, [value])

  useEffect(() => {
    if (!open) return undefined
    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const cells = useMemo(() => {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
    const offset = (first.getDay() + 6) % 7
    const days = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate()
    return Array.from({ length: 42 }, (_, index) => {
      const date = new Date(cursor.getFullYear(), cursor.getMonth(), index - offset + 1)
      return { date, inMonth: date.getMonth() === cursor.getMonth(), day: date.getDate() }
    }).slice(0, Math.ceil((offset + days) / 7) * 7)
  }, [cursor])

  const choose = (date) => {
    onChange?.(isoOf(date))
    setOpen(false)
  }

  return (
    <div ref={rootRef} data-testid="baseline-date-picker" className="relative">
      <button
        type="button"
        aria-label="选择核查基准日"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled || readOnly}
        onClick={() => setOpen((current) => !current)}
        className={`flex h-10 min-h-10 w-full items-center justify-between rounded-lg border border-[#DCE5EE] bg-white px-2.5 text-left text-[16px] outline-none transition-colors focus:border-[#174C7E] ${displayOf(value) ? 'text-[#17283D]' : 'text-[#8291A1]'} disabled:cursor-not-allowed disabled:bg-[#FBFAF7]`}
      >
        <span>{displayOf(value) || 'yyyy/mm/dd'}</span>
        <CalendarDays className="h-4 w-4 shrink-0 text-[#587089]" aria-hidden="true" />
      </button>
      {open && (
        <div role="dialog" aria-label="核查基准日日期选择器" className="absolute left-0 top-[calc(100%+6px)] z-40 w-[280px] rounded-lg border border-[#DCE5EE] bg-white p-3 shadow-[0_12px_30px_rgba(23,40,61,0.16)]">
          <div className="flex items-center justify-between">
            <button type="button" aria-label="上个月" onClick={() => setCursor((date) => new Date(date.getFullYear(), date.getMonth() - 1, 1))} className="rounded p-1 text-[#587089] hover:bg-[#EAF2FB]"><ChevronLeft className="h-4 w-4" /></button>
            <strong className="text-[13px] text-[#17283D]">{cursor.getFullYear()}年{cursor.getMonth() + 1}月</strong>
            <button type="button" aria-label="下个月" onClick={() => setCursor((date) => new Date(date.getFullYear(), date.getMonth() + 1, 1))} className="rounded p-1 text-[#587089] hover:bg-[#EAF2FB]"><ChevronRight className="h-4 w-4" /></button>
          </div>
          <div className="mt-2 grid grid-cols-7 text-center text-[11px] text-[#8291A1]">
            {['一', '二', '三', '四', '五', '六', '日'].map((day) => <span key={day} className="py-1">{day}</span>)}
          </div>
          <div className="grid grid-cols-7 gap-1 text-center">
            {cells.map(({ date, inMonth, day }) => {
              const active = selected && isoOf(selected) === isoOf(date)
              const isToday = isoOf(today) === isoOf(date)
              return <button key={isoOf(date)} type="button" aria-current={active ? 'date' : undefined} data-date={isoOf(date)} disabled={!inMonth} onClick={() => choose(date)} className={`h-8 rounded-md text-[12px] transition-colors ${!inMonth ? 'text-[#CBD5E1]' : active ? 'bg-[#174C7E] font-bold text-white ring-2 ring-[#8DB9E6] ring-offset-1' : isToday ? 'border-2 border-[#174C7E] font-semibold text-[#174C7E]' : 'text-[#385A77] hover:bg-[#EAF2FB]'}`}>{day}</button>
            })}
          </div>
          <div className="mt-2 flex items-center justify-between border-t border-[#EEF2F7] pt-2 text-[12px]">
            <button type="button" onClick={() => { onChange?.(''); setOpen(false) }} className="text-[#587089] hover:text-[#174C7E]">清除</button>
            <button type="button" onClick={() => choose(today)} className="text-[#174C7E] hover:underline">今天</button>
          </div>
        </div>
      )}
    </div>
  )
}
