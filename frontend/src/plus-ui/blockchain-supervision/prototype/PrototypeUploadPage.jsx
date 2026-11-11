import { useRef, useState } from 'react'
import { UploadCloud } from 'lucide-react'
import PrototypeDatePicker from './PrototypeDatePicker'
import { buildUploadReadiness } from './prototypeViewModel'
import MaterialPreviewModal from '../components/MaterialPreviewModal'
import { zipPreparationFailureMessages } from '../lib/materialState'

const ROLE_OPTIONS = [
  ['general', '总包合同'],
  ['sub', '专业分包合同'],
  ['labor', '劳务分包合同'],
  ['packet', '报审资料包'],
]

const ROW_ROLE_OPTIONS = [...ROLE_OPTIONS, ['unknown', '待归类']]

function formatFileSize(sizeKb) {
  const kb = Number(sizeKb)
  if (!Number.isFinite(kb) || kb <= 0) return ''
  if (kb < 1024) return `${Math.round(kb)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(mb >= 10 ? 0 : 1).replace(/\.0$/, '')} MB`
}

export default function PrototypeUploadPage({
  task,
  uploadRole = 'general',
  uploadSubject = '',
  subjectOptions = [],
  busy = false,
    preparing = false,
  parseEtaLabel = '预计剩余 估算中…',
  supplement = null,
  // 只读查看态：任务已开始后从流程条回到「上传材料」只看清单，
  // 不触发任何写接口；要真正改材料必须显式点「修改材料」并确认作废。
  readOnly = false,
  onSectionChange,
  onRoleChange,
  onSubjectChange,
  onMaterialRoleChange,
  onMaterialSubjectChange,
  onSupplementCancel,
  onSelectFiles,
  onDropFiles,
  onRemove,
  onReplace,
  onStart,
  onRequestEdit,
  onExit,
    onReset,
    resetting = false,
}) {
  const materials = task?.materials || []
  const parseProgress = task?.progress?.current_task || task?.progress?.current || null
  const hasPageProgress = Number.isFinite(Number(parseProgress?.current_page))
    && Number(parseProgress.current_page) > 0
  const totalPages = Number(parseProgress?.total_pages)
  const currentPage = Number(parseProgress?.current_page)
  const workTotal = Number(parseProgress?.total_work_pages)
  const workDone = Number(parseProgress?.done_pages)
  const pagePercent = workTotal > 0 && Number.isFinite(workDone) ? Math.min(100, Math.round(workDone / workTotal * 100)) : null
  const readiness = buildUploadReadiness({ validation: task?.validation, materials, zipPreparation: task?.zipPreparation })
  const zipFailureMessages = zipPreparationFailureMessages(task?.zipPreparation)
  const [previewId, setPreviewId] = useState(null)
  const replaceRef = useRef(null)
  const replaceTargetRef = useRef(null)
  const pendingReplacementTargets = new Set(materials.map((item) => item.replacementTargetId).filter((value) => value != null))

  const requestReplace = (materialId) => {
    replaceTargetRef.current = materialId
    if (!replaceRef.current) return
    replaceRef.current.value = ''
    replaceRef.current.click()
  }

  const submitReplace = (event) => {
    const file = event.target.files?.[0]
    const materialId = replaceTargetRef.current
    replaceTargetRef.current = null
    if (file && materialId != null) onReplace?.(materialId, file)
  }

  return (
    <section data-testid="prototype-upload-page" className="screen w-full px-6 py-4 pb-[60px]"
      onDragOver={(event) => event.preventDefault()} onDrop={(event) => event.preventDefault()}>
        <div className="mb-3.5 flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="mb-1 text-2xl font-bold tracking-[.01em]">{readOnly ? '报审材料' : '上传报审材料'}</h1>
            <p className="text-[15px] text-[#6B7280]">{readOnly ? '查看本轮核查所用的材料清单；材料定位与匹配在核查阶段完成。' : '上传本次报审文件并确认归类；材料定位与匹配在核查阶段完成。'}</p>
          </div>
          <button type="button" disabled={resetting} onClick={onReset} className="min-h-9 shrink-0 rounded-[4px] border border-[#DCE5EE] px-3 py-1.5 text-xs text-[#587089] hover:bg-[#F4F7FA] disabled:opacity-50">
            重新开始
          </button>
      </div>

      {supplement ? <div data-testid="supplement-context" className="mb-3 rounded-lg border border-[#F0DCB4] bg-[#FFF9ED] px-4 py-3 text-[14px]"><div className="flex items-start gap-3"><div><b>本次补件：{supplement.label}</b><div className="mt-1 text-[#6B7280]">{supplement.hint || '请选择需要补充的材料'}</div></div><button type="button" className="ml-auto text-[#6B5A45] underline" onClick={onSupplementCancel}>取消补件</button></div>{supplement.subjectName ? <div className="mt-2 text-[#6B5A45]">关联主体：{supplement.subjectName}{supplement.subjectAutoSelected ? '（已自动选择）' : ''}</div> : <div className="mt-2 text-[#92400E]">关联主体待选择</div>}</div> : null}

      {materials.filter((material) => material.supersededById == null && (material.subjectConflict || material.subjectUnresolved)).map((material) => (
        <div key={material.id} role="alert" className="mb-3 rounded-lg border border-[#F0DCB4] bg-[#FFF9ED] px-4 py-3 text-[14px] text-[#92400E]">
          <b>{material.subjectConflict ? '补件未通过' : '补件主体无法确认'}：{material.fileName}</b>
          {material.supplementLabel ? <p>补件范围：{material.supplementLabel}</p> : null}
          {pendingReplacementTargets.has(material.id)
            ? <p>新材料已上传，请查看新材料状态。</p>
            : material.subjectConflict
            ? <p>材料识别主体：{material.subjectConflict.detected_subject_name || '待确认'}；当前补件主体：{material.subjectConflict.expected_subject_name || '待确认'}。两者不一致，请重新上传对应主体的材料。</p>
            : <p>{material.subjectUnresolved.message || '无法确认材料所属主体，请核对证照或重新上传清晰材料。'}</p>}
          {!readOnly && !pendingReplacementTargets.has(material.id) && (material.replacementTargetId != null
            ? <button type="button" className="underline" disabled={busy} onClick={() => onRemove?.(material.id)}>移除此材料后重新替换原材料</button>
            : <button type="button" className="underline" disabled={busy} onClick={() => requestReplace(material.id)}>重新上传材料</button>)}
        </div>
      ))}
      {zipFailureMessages.length ? (
        <div data-testid="zip-preparation-failure" role="alert" className="mb-3 rounded-lg border border-[#F5C6C6] bg-[#FDECEA] px-4 py-3 text-[14px] text-[#C62828]">
          <b>资料包准备失败</b>
          <ul className="mt-1 list-disc pl-5">
            {zipFailureMessages.map((message) => <li key={message}>{message}</li>)}
          </ul>
          <p className="mt-1">请移除失败资料包后重新上传，再开始核查。</p>
        </div>
      ) : null}
      <section className="mb-3.5 overflow-hidden rounded-[10px] border border-[#E9E3D9] bg-white">
        <header className="flex items-center justify-between gap-2.5 border-b border-[#F2EDE5] px-4 py-3"><h2 className="text-[17px]">标段信息</h2><span className="text-[14px] text-[#9CA3AF]">选填 · 用于报告标段锚点与合同字段比对</span></header>
          <div className="grid grid-cols-1 gap-y-3 gap-x-[18px] p-4 md:grid-cols-3">
          {[['projectName', '项目名称', '如 G15 公路嘉金段改扩建工程'], ['contractSection', '合同段', '如 主线施工 8 标']].map(([key, label, placeholder]) => (
            <label key={key} className="flex flex-col gap-1 text-[14px]"><span>{label}</span><input value={task?.section?.[key] || ''} placeholder={placeholder} disabled={busy || readOnly} onChange={(event) => onSectionChange?.(key, event.target.value)} className="min-h-[38px] rounded-lg border border-[#E9E3D9] px-2.5 text-[16px] outline-none focus:border-[#8B7355]" /></label>
          ))}
          <div className="flex flex-col gap-1 text-[13px] font-semibold text-[#8291A1]" data-testid="prototype-baseline-date-field-correct">
            <span>核查基准日</span>
            <PrototypeDatePicker value={task?.section?.baselineDate || ''} disabled={busy} readOnly={readOnly} onChange={(value) => onSectionChange?.('baselineDate', value)} />
            <small className="text-[13px] font-normal text-[#9CA3AF]">以该日期有效的规则版本核查</small>
          </div>
          <label className="flex flex-col gap-1 text-[14px]"><span>核查基准日</span><input aria-label="核查基准日" type="date" value={task?.section?.baselineDate || ''} disabled={busy || readOnly} onChange={(event) => onSectionChange?.('baselineDate', event.target.value)} className="min-h-[38px] rounded-lg border border-[#E9E3D9] px-2.5 text-[16px] outline-none focus:border-[#8B7355]" /><small className="text-[13px] text-[#9CA3AF]">以该日期有效的规则版本核查</small></label>
        </div>
      </section>

      <section data-testid="material-ledger" className="overflow-hidden rounded-[10px] border border-[#E9E3D9] bg-white">
        <header className="flex items-center justify-between gap-2.5 border-b border-[#F2EDE5] px-4 py-3"><h2 className="text-[17px]">材料核验台账</h2><span className="text-[14px] text-[#9CA3AF]">{readOnly ? `${materials.length} 份 · 只读` : `${materials.length} 份 · 归类可逐份更正`}</span></header>
        {readOnly ? null : (<div className="flex flex-wrap items-end gap-3 p-3.5 pb-2.5">
          <label className="flex min-w-[160px] flex-col gap-1 text-[14px]"><span>资料类别</span><select data-testid="upload-category" value={uploadRole} disabled={busy} onChange={(event) => onRoleChange?.(event.target.value)} className="min-h-[38px] rounded-lg border border-[#E9E3D9] bg-white px-2.5 text-[16px]">{ROLE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          {uploadRole === 'packet' ? <label className="flex min-w-[220px] flex-col gap-1 text-[14px]"><span>关联分包主体</span>{subjectOptions.length ? <select value={uploadSubject} disabled={busy} onChange={(event) => onSubjectChange?.(event.target.value)} className="min-h-[38px] rounded-lg border border-[#E9E3D9] bg-white px-2.5 text-[16px]"><option value="">请选择已建立的主体</option>{subjectOptions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select> : <span className="py-2 text-[14px] text-[#9CA3AF]">请先上传专业分包或劳务分包合同</span>}</label> : null}
        </div>)}
        {readOnly ? null : (<div data-testid="upload-drop-zone" onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => { event.preventDefault(); event.stopPropagation(); if (!busy && !readOnly) onDropFiles?.(event.dataTransfer?.files) }}>
          <button type="button" disabled={busy} onClick={onSelectFiles} className="mx-3.5 flex w-[calc(100%-28px)] items-center gap-3 rounded-lg border-[1.5px] border-dashed border-[#D8CDBD] bg-[#FDFBF7] px-[18px] py-[15px] text-left hover:border-[#8B7355] disabled:opacity-50 max-lg:flex-wrap"><UploadCloud className="h-6 w-6 shrink-0 text-[#8B7355]" /><span className="flex min-w-0 flex-1 flex-col gap-0.5"><b className="text-[15px]">拖拽 PDF 或 ZIP 到此，或点击选择</b><span className="text-[14px] text-[#6B7280]">可多选 · 按所选类别上传，上传后可逐份更正</span></span><span className="ml-auto whitespace-nowrap text-[13px] text-[#9CA3AF] max-lg:ml-0 max-lg:basis-full max-lg:whitespace-normal">支持 PDF 与 ZIP 报审资料包，单文件不超过 200 MB</span></button></div>)}
        {task?.subjectTypeNotices?.length ? (
          <ul data-testid="subject-type-notices" className="mx-3.5 mb-2 rounded-lg border border-[#F0DCB4] bg-[#FFF9ED] px-3.5 py-2.5 text-[13px] leading-relaxed text-[#7C4A03]">
            {task.subjectTypeNotices.map((notice) => (
              <li key={notice.subjectId}>
                <b>{notice.label}</b>：{notice.reason}。已按{notice.chosenTypeLabel}核查，不阻断开始；如归类有误，可在台账里更正归类。
              </li>
            ))}
            <div className="flex flex-col gap-1 text-[13px] font-semibold text-[#8291A1]" data-testid="prototype-baseline-date-field">
              <span>核查基准日</span>
              <PrototypeDatePicker value={task?.section?.baselineDate || ''} disabled={busy} readOnly={readOnly} onChange={(value) => onSectionChange?.('baselineDate', value)} />
              <small className="text-[13px] font-normal text-[#9CA3AF]">以该日期有效的规则版本核查</small>
            </div>
          </ul>
        ) : null}
        {materials.length ? <div className="p-3.5"><div className="mb-1.5 flex items-center gap-2 text-[14px]"><b>已上传 {materials.length} 份</b><span className="text-[#6B7280]">{readOnly ? '只读查看 · 需要增删或替换请点页尾「修改材料」' : '确认有分包合同即可开始；附件缺口在核查中逐项列出'}</span></div><div className="overflow-hidden rounded border border-[#F2EDE5]"><div className="grid grid-cols-[minmax(0,2.1fr)_minmax(0,1.25fr)_minmax(0,1.25fr)_minmax(0,1.1fr)] border-b border-[#F2EDE5] px-3 py-2 text-[13px] text-[#9CA3AF]"><span>材料文件</span><span>归类</span><span>主体</span><span className="text-right">操作</span></div>{materials.map((material) => <div key={material.id} data-testid={`prototype-material-${material.id}`} className="grid grid-cols-[minmax(0,2.1fr)_minmax(0,1.25fr)_minmax(0,1.25fr)_minmax(0,1.1fr)] items-center gap-2 border-b border-[#F2EDE5] px-3 py-3 text-[14px] last:border-b-0"><span className="flex min-w-0 flex-col"><b className="truncate" title={material.fileName}>{material.fileName}</b>{material.supplementLabel ? <small className="text-[12px] text-[#8A5E1C]">补件范围：{material.supplementLabel}</small> : null}{material.supersededById != null ? <small className="text-[12px] text-[#9CA3AF]">已被替换</small> : material.parseState === "failed" && material.replacementTargetId != null ? <small className="text-[12px] text-[#C62828]">替换失败：{material.parseFailureMessage || "材料无法读取，请移除后重新上传"}</small> : material.parseState === "failed" ? <small className="text-[12px] text-[#C62828]">读取失败：{material.parseFailureMessage || "材料无法读取，请检查文件是否损坏"}</small> : material.replacementTargetId != null ? <small className="text-[12px] text-[#92400E]">替换待生效</small> : pendingReplacementTargets.has(material.id) ? <small className="text-[12px] text-[#9CA3AF]">新版本已上传</small> : null}</span>{readOnly ? <span className="truncate text-[#4B5563]" title={material.roleLabel || ''}>{material.roleLabel || '待归类'}</span> : <select aria-label="材料归类" value={material.role || 'unknown'} disabled={busy} onChange={(event) => onMaterialRoleChange?.(material.id, event.target.value)} className="min-h-[32px] min-w-0 rounded border border-[#E9E3D9] bg-white px-1.5 text-[13px]">{ROW_ROLE_OPTIONS.some(([value]) => value === material.role) ? null : <option value={material.role || 'unknown'}>{material.roleLabel || '待归类'}</option>}{ROW_ROLE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>}{material.role === 'packet' ? (readOnly ? <span className="truncate text-[#4B5563]" title={material.subjectName || ''}>{material.subjectName || '—'}</span> : subjectOptions.length ? <select aria-label="材料主体" value={material.subjectId != null ? String(material.subjectId) : ''} disabled={busy} onChange={(event) => onMaterialSubjectChange?.(material.id, event.target.value)} className="min-h-[32px] min-w-0 rounded border border-[#E9E3D9] bg-white px-1.5 text-[13px]"><option value="">请选择主体</option>{subjectOptions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select> : <span className="text-[13px] text-[#9CA3AF]">先上传分包合同</span>) : <span className="truncate text-[#9CA3AF]" title={material.subjectName || ''}>{material.subjectName || '—'}</span>}<span className="flex justify-end gap-1.5 whitespace-nowrap text-[13px]">{material.canPreview ? <button type="button" className="text-[#6B5A45]" onClick={() => setPreviewId(material.id)}>查看</button> : null}{!readOnly && material.supersededById == null ? <><button type="button" className="text-[#6B5A45]" disabled={busy} onClick={() => requestReplace(material.id)}>替换</button><button type="button" className="text-[#6B5A45]" disabled={busy} onClick={() => onRemove?.(material.id)}>移除</button></> : null}</span></div>)}</div></div> : null}
        {!readOnly && materials.length ? <div className={`mx-3.5 mb-3.5 rounded-lg border px-3.5 py-3 ${preparing ? 'border-[#8DB9E6] bg-[#EAF2FB]' : readiness.ready ? 'border-[#CFE8D0] bg-[#F3FAF3]' : 'border-[#F0DCB4] bg-[#FFF9ED]'}`} data-testid="prototype-upload-readiness"><b>{preparing ? '材料已受理，正在读取内容' : readiness.ready ? '可以开始核查' : '暂不能开始，先处理以下事项'}</b>{preparing ? <p className="mt-1 text-[14px] text-[#587089]">系统正在读取 PDF 内容、页码和判章信息，读取完成后会自动进入智能核查。</p> : readiness.reasons.length ? <ul className="mt-1 list-disc pl-5 text-[14px] text-[#92400E]">{readiness.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p className="mt-1 text-[14px] text-[#6B7280]">附件不齐不影响开始，核查会逐项列出缺口并提供补件。</p>}</div> : null}
        {preparing && hasPageProgress ? <div data-testid="material-page-progress" className="mx-3.5 mb-3.5 rounded-lg border border-[#C9DDF2] bg-[#F5FAFF] px-3.5 py-3"><div className="flex items-center justify-between gap-3 text-[14px]"><b>{parseProgress.source === 'text' ? '文本层扫描' : '文字识别'}：{parseProgress.file_name || '当前文件'}</b><span className="text-[#587089]">{parseProgress.source === 'failed' ? `第 ${currentPage} 页读取失败` : `最近处理第 ${currentPage} 页`}{totalPages > 0 ? ` / 共 ${totalPages} 页` : ''}</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-[#DCEAF8]"><div className="h-full rounded-full bg-[#5A94C9] transition-all" style={{ width: `${pagePercent ?? 35}%` }} /></div><div className="mt-1.5 flex justify-between text-[13px] text-[#587089]"><span>{pagePercent == null ? '阶段总量确认中' : `本阶段已处理 ${workDone} / ${workTotal} 页（${pagePercent}%）`}</span><span>本阶段读取：{parseEtaLabel}</span></div></div> : null}
      </section>

      {readOnly ? (
        <div className="mt-3.5 flex flex-wrap items-center gap-3 rounded-[10px] border border-[#E9E3D9] bg-white px-4 py-3.5" data-testid="prototype-materials-view-bar">
          <div className="min-w-[260px] flex-1"><b>材料已锁定为查看态</b><p className="mt-0.5 text-[14px] text-[#6B7280]">如需增删或替换材料，请点「修改材料」——这会作废本轮结论并留档为历史版本，需要重新核查。</p></div>
          <button type="button" data-testid="materials-back" onClick={onExit} className="min-h-[38px] rounded-lg border border-[#E9E3D9] px-4 text-[14px] text-[#6B5A45]">返回核查</button>
          <button type="button" data-testid="materials-edit" onClick={onRequestEdit} className="min-h-[38px] rounded-lg border border-[#8B7355] px-4 text-[14px] text-[#8B7355]">修改材料</button>
        </div>
      ) : (
        <div className={`mt-3.5 flex flex-wrap items-center gap-3 rounded-[10px] border px-4 py-3.5 ${preparing ? 'border-[#8DB9E6] bg-[#EAF2FB]' : readiness.ready ? 'border-[#CFE8D0] bg-[#F3FAF3]' : 'border-[#E9E3D9] bg-white'}`} data-testid="prototype-upload-start-bar"><div className="min-w-[260px] flex-1"><b>{preparing ? '正在读取材料，准备智能核查' : !materials.length ? '先上传报审材料' : readiness.ready ? '材料已就绪，可以开始核查' : '先处理上方阻断项'}</b><p className="mt-0.5 text-[14px] text-[#6B7280]">{preparing ? '读取完成后系统会自动进入智能核查页面。' : '系统将按五步核查定位册内材料、比对报审附件要求。'}</p></div><button type="button" data-testid="start-review" disabled={!readiness.ready || busy || preparing} onClick={onStart} className="min-h-[38px] rounded-lg bg-[#8B7355] px-4 text-[16px] text-white disabled:cursor-not-allowed disabled:opacity-45">{preparing ? '读取中…' : '开始核查'}</button></div>
      )}

      <input ref={replaceRef} type="file" hidden accept=".pdf,.zip" onChange={submitReplace} />
      {previewId != null ? <MaterialPreviewModal materials={materials.map((item) => ({ id: item.id, material_id: item.id, file_name: item.fileName, parse_state: item.parseState }))} activeMaterialId={previewId} requireParsed={false} onSelect={(id) => setPreviewId(id)} onClose={() => setPreviewId(null)} /> : null}
    </section>
  )
}
