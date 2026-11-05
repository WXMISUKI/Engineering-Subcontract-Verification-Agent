import { useEffect, useState } from 'react'

/** 宽屏断点：与 ReviewWorkbenchLayout 的 Tailwind xl(1280px) 同步（设计 100 §6）。 */
export const WIDE_QUERY = '(min-width: 1280px)'

function readMatches(query, fallback) {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return fallback
  try {
    const mql = window.matchMedia(query)
    return typeof mql?.matches === 'boolean' ? mql.matches : fallback
  } catch {
    return fallback
  }
}

/**
 * 订阅 CSS media query。
 *
 * 首帧同步读取 `matchMedia(query).matches`，避免「先按宽屏渲染再切抽屉」的闪烁：
 * 浏览器首帧就拿到真实宽度；SSR / 无 matchMedia 的环境按 `fallback`（默认宽屏）渲染。
 * 测试（jsdom）用 `window.matchMedia = () => ({matches: ...})` 打桩即可驱动窄/宽两种形态。
 *
 * @param {string} query CSS media query
 * @param {{fallback?: boolean}} [options] 无 matchMedia 时的初始值（默认 true=宽屏）
 * @returns {boolean}
 */
export function useMediaQuery(query = WIDE_QUERY, { fallback = true } = {}) {
  const [matches, setMatches] = useState(() => readMatches(query, fallback))

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    let mql = null
    try {
      mql = window.matchMedia(query)
    } catch {
      return undefined
    }
    if (!mql) return undefined

    const sync = () => setMatches(typeof mql.matches === 'boolean' ? mql.matches : fallback)
    sync()

    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', sync)
      return () => mql.removeEventListener('change', sync)
    }
    // 兼容旧 Safari：addListener / removeListener
    if (typeof mql.addListener === 'function') {
      mql.addListener(sync)
      return () => mql.removeListener(sync)
    }
    return undefined
  }, [query, fallback])

  return matches
}

export default useMediaQuery