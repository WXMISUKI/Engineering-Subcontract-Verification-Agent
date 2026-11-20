import { useEffect, useState } from 'react'
import { BS_EVENT_TYPES, bsEventsUrl } from '../lib/api'

// 浏览器原生重连携带Last-Event-ID；刷新从0回放。读取事件从不启动业务作业。
export function useBsEventStream(taskId, runVersion, { retrospective = false } = {}) {
  const [snapshot, setSnapshot] = useState(null)
  const key = `${taskId}:${runVersion || ''}:${retrospective}`
  useEffect(() => {
    if (taskId == null || typeof EventSource === 'undefined' || (retrospective && !runVersion)) return undefined
    let active = true
    let maxSeq = 0
    const es = new EventSource(bsEventsUrl(taskId, 0), { withCredentials: true })
    setSnapshot({ key, events: [] })
    const onFrame = (event) => {
      if (!active) return
      let dto
      try { dto = JSON.parse(event.data) } catch { return }
      if (!Number.isInteger(dto.sequence) || dto.sequence <= maxSeq) return
      maxSeq = dto.sequence
      if (retrospective && dto.run_id !== runVersion) return
      setSnapshot((prev) => ({ key, events: [...(prev?.key === key ? prev.events : []), dto] }))
    }
    for (const type of BS_EVENT_TYPES) es.addEventListener(type, onFrame)
    es.addEventListener('stream.reset', () => {
      if (!active) return
      maxSeq = 0
      setSnapshot({ key, events: [] })
    })
    es.addEventListener('done', () => es.close())
    return () => { active = false; es.close() }
  }, [taskId, runVersion, retrospective, key])
  return snapshot?.key === key ? snapshot.events : []
}
