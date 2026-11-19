import { useEffect, useState } from 'react'

/**
 * 顶栏模型选择器：只展示目录中可选、启用且已定价的对话模型。
 * 切换写入部署级默认模型；后续模型调用使用新选择，已生成的报告不被改写。
 */
export default function ModelSelect({ value, onChange }) {
  const [models, setModels] = useState([])
  const [current, setCurrent] = useState(value || '')
  const [phase, setPhase] = useState('loading')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetch('/api/models', { credentials: 'include' })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error('模型目录不可用'))))
      .then((data) => {
        if (!active) return
        const list = Array.isArray(data.models) ? data.models : []
        setModels(list)
        setCurrent(data.current || list[0]?.code || '')
        setPhase('ready')
        if (data.current) onChange?.(data.current)
      })
      .catch(() => {
        if (!active) return
        setPhase('error')
        setError('模型目录不可用')
      })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (value) setCurrent(value)
  }, [value])

  const handleChange = async (event) => {
    const next = event.target.value
    const previous = current
    setCurrent(next)
    setPhase('saving')
    setError('')
    try {
      const response = await fetch('/api/model', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_code: next }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || '模型切换失败')
      }
      onChange?.(next)
      setPhase('ready')
    } catch (exc) {
      setCurrent(previous)
      setPhase('ready')
      setError(exc.message)
    }
  }

  if (phase === 'loading') {
    return <span className="text-xs text-secondary" title="模型由部署方配置">正在检查模型</span>
  }
  if (!models.length) {
    return <span className="text-xs text-secondary" title={error || '模型由部署方配置'}>模型未配置</span>
  }
  const selected = models.some((model) => model.code === current) ? current : ''
  return (
    <label className="inline-flex items-center gap-1 text-xs text-secondary" title="切换部署默认模型；后续模型调用使用新选择">
      <span className="shrink-0">模型</span>
      <select
        aria-label="切换模型"
        value={selected}
        onChange={handleChange}
        disabled={phase === 'saving'}
        className="h-7 max-w-[180px] rounded border border-[#D8CBB8] bg-white px-1.5 text-xs text-[#3B332B] outline-none disabled:opacity-60"
      >
        {!selected && <option value="">请选择</option>}
        {models.map((model) => (
          <option key={model.code} value={model.code}>{model.display_name || model.code}</option>
        ))}
      </select>
      {error && <span className="text-[11px] text-red-600" title={error}>!</span>}
    </label>
  )
}
