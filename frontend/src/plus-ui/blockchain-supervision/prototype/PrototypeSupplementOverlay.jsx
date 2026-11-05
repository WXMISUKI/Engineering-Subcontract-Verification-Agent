import { useEffect, useRef, useState } from 'react'

/**
 * v3 原型的「上传 / 补件」覆盖层（静态原型 modal width:min(560px,100%)）。
 * 覆盖层只负责收集补件意图和文件；是否作废当前审查代、何时写入后端由宿主工作台统一处理。
 */
export default function PrototypeSupplementOverlay({ materialLabel = '材料', stepLabel = '第 1 步 · 要素完整性核查', uploadCategory = null, replacesMaterialId = null, subjectId = null, subjectOptions = [], packetId = null, onCancel, onSubmit }) {
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [note, setNote] = useState('')
  const [selectedSubject, setSelectedSubject] = useState(subjectId == null ? '' : String(subjectId))
  const requiresSubject = uploadCategory === 'packet' && subjectId == null && subjectOptions.length > 0
  const subjectLabel = subjectOptions.find((subject) => String(subject.id) === String(subjectId))?.label

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCancel?.()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onCancel])

  const submit = () => {
    if (!file || (requiresSubject && !selectedSubject)) return
    onSubmit?.({ file, note, materialLabel, stepLabel, uploadCategory, replacesMaterialId, subjectId: selectedSubject ? Number(selectedSubject) : subjectId, packetId })
  }

  return (
    <div className="fixed bottom-0 left-0 right-[15px] top-0 z-50 flex items-center justify-center bg-[rgba(15,23,42,0.4)] p-5" data-testid="prototype-supplement-overlay" onClick={(event) => { if (event.target === event.currentTarget) onCancel?.() }}>
      <div role="dialog" aria-modal="true" aria-labelledby="prototype-supplement-title" className="w-[min(560px,100%)] rounded-[10px] border border-[#E9E3D9] bg-white shadow-xl">
        <header className="flex items-center gap-3 border-b border-[#F2EDE5] px-[15px] py-[13px]">
          <h2 id="prototype-supplement-title" className="text-[15px] font-bold text-[#1A1A1A]">上传 / 补件</h2>
          <span className="rounded-full border border-[#F0DCB8] bg-[#FDFAF4] px-2 py-0.5 text-[11px] text-[#92400E]">{materialLabel}</span>
          <button type="button" className="sr-only" onClick={onCancel}>关闭</button>
        </header>
        <div className="space-y-3 px-[15px] pb-[22px] pt-[22px] text-[13px] text-[#4B5563]">
          <div>
            <label className="font-medium text-[#1A1A1A]" htmlFor="prototype-supplement-file">材料类别</label>
            <p className="mt-1 text-[12px] text-[#6B7280]">{materialLabel} · {stepLabel}</p>
          </div>
          <div>
            <label className="font-medium text-[#1A1A1A]" htmlFor="prototype-supplement-file">选择文件（PDF / ZIP）</label>
            <input ref={inputRef} id="prototype-supplement-file" type="file" accept=".pdf,.zip,application/pdf,application/zip" onChange={(event) => setFile(event.target.files?.[0] || null)} className="mt-1 block w-full rounded border border-[#E9E3D9] px-2.5 py-2 text-[13px]" />
          </div>
          {subjectId != null && <p className="text-[#1A1A1A]">补件主体：{subjectLabel || `主体 ${subjectId}（名称待确认）`}</p>}
          {requiresSubject && <label className="block font-medium text-[#1A1A1A]">关联分包主体
            <select value={selectedSubject} onChange={(event) => setSelectedSubject(event.target.value)} className="mt-1 block w-full rounded border border-[#E9E3D9] px-2.5 py-2">
              <option value="">请选择本次补件的主体</option>
              {subjectOptions.map((subject) => <option key={subject.id} value={subject.id}>{subject.label}</option>)}
            </select>
          </label>}
          <div>
            <label className="font-medium text-[#1A1A1A]" htmlFor="prototype-supplement-note">补件说明（选填）</label>
            <textarea id="prototype-supplement-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：补充 2026-10 社保缴费证明" className="mt-1 min-h-[72px] w-full rounded border border-[#E9E3D9] px-2.5 py-2 text-[13px] outline-none focus:border-[#8B7355]" />
          </div>
          <div className="rounded border border-[#F0DCB8] bg-[#FDFAF4] px-3 py-2 text-[12px] leading-relaxed text-[#92400E]">补件核验后将重新核查受影响的问题，原有结果会留档。</div>
        </div>
        <footer className="flex justify-end gap-2 border-t border-[#F2EDE5] px-[15px] py-[11px]">
          <button type="button" className="min-h-[34px] rounded border border-[#E9E3D9] px-3 py-[7px] text-[13px] text-[#6B5A45]" onClick={onCancel}>取消</button>
          <button type="button" data-testid="prototype-supplement-submit" disabled={!file || (requiresSubject && !selectedSubject)} className="min-h-[34px] rounded bg-[#8B7355] px-3 py-[7px] text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-45" onClick={submit}>上传并标记待重跑</button>
        </footer>
      </div>
    </div>
  )
}
