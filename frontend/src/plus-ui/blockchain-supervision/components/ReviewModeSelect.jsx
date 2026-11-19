export default function ReviewModeSelect({ value = 'admission', onChange, locked = false }) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-[#587089]" title={locked ? '核查阶段已随任务保存；切换阶段请新建核查' : '选择本次核查阶段'}>
      <span>核查阶段</span>
      <select aria-label="核查阶段" value={value} disabled={locked} onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-[#DCE5EE] bg-white px-2 py-1.5 text-[#17283D] disabled:bg-[#F4F6F8]">
        <option value="admission">准入阶段</option>
        <option value="contract_retrospective">合同倒查阶段</option>
      </select>
    </label>
  )
}
