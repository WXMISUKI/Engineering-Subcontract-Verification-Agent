import { useEffect, useState } from 'react'

export default function ModelSelect() {
  const [label, setLabel] = useState('正在检查模型')
  useEffect(() => {
    let active = true
    fetch('/api/model', { credentials: 'include' }).then(response => response.ok ? response.json() : Promise.reject())
      .then(data => { if (active) setLabel(data.model || '模型未配置') })
      .catch(() => { if (active) setLabel('模型未配置') })
    return () => { active = false }
  }, [])
  return <span className="text-xs text-secondary" title="模型由部署方配置">{label}</span>
}
