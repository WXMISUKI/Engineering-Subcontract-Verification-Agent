import { useState } from 'react'
import { getBsReviewVersion } from '../lib/api'

const labels = { hit: '不符合', miss: '符合', undecidable: '无法核实' }

/** Read-only baseline: never mix an in-progress supplement with current findings. */
export default function PreviousSupplementReview({ taskId, versionNo }) {
  const [version, setVersion] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  if (!taskId || !Number.isInteger(versionNo)) return null
  const show = async () => {
    if (open) { setOpen(false); return }
    setOpen(true)
    if (version) return
    setBusy(true)
    setError('')
    try { setVersion(await getBsReviewVersion(taskId, versionNo)) }
    catch { setError('上次核查结果暂时无法读取，请稍后重试') }
    finally { setBusy(false) }
  }
  return <section className="mb-3 rounded-lg border border-[#E9D4AE] bg-[#FFF9EF] p-3 text-sm" data-testid="previous-supplement-review">
    <div className="flex flex-wrap items-center gap-2">
      <span>上次核查结果（不含本次补件，仅供查看）</span>
      <button type="button" onClick={show} className="rounded border border-[#A18460] px-2 py-1 text-[#674C30]">{open ? '收起上次核查结果' : '查看上次核查结果'}</button>
    </div>
    {open && <div className="mt-2 max-h-56 overflow-y-auto">
      {busy && <p role="status">正在读取上次结果…</p>}
      {error && <p role="alert">{error}</p>}
      {version && <ul className="space-y-1">{(version.snapshot?.findings || []).map((finding, index) => <li key={finding.finding_id ?? index}>
        <span>{finding.item_id} · {labels[finding.status] || '状态待核实'} · {finding.message}</span>
      </li>)}</ul>}
    </div>}
  </section>
}
