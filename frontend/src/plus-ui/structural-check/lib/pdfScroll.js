/**
 * Scroll a PDF page slot inside its dedicated scroll container.
 * Calling Element.scrollIntoView() here would also scroll the workbench shell.
 */
export function scrollSlotWithinContainer(container, slot) {
  if (!container || !slot || typeof container.scrollTo !== 'function') return false
  const top = Math.max(Number(slot.offsetTop) - Number(container.clientTop || 0), 0)
  container.scrollTo({ top, behavior: 'auto' })
  return true
}

/** Keep the same document position within a page when its display scale changes. */
export function scaledPageScrollTop(slotTop, pageOffset, oldScale, newScale) {
  if (![slotTop, pageOffset, oldScale, newScale].every(Number.isFinite) || oldScale <= 0 || newScale <= 0) return null
  return Math.max(slotTop + pageOffset * newScale / oldScale, 0)
}
