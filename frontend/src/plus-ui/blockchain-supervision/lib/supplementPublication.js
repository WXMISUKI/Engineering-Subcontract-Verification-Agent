/**
 * Whether a running supplement generation is still waiting for its candidate
 * publication payload to become available.
 *
 * A candidate can be ready before it is published, so the published
 * generation id may intentionally still point at the previous version.
 */
export function supplementPublicationPending({ plan, publication } = {}) {
  if (plan?.status !== 'running') return false
  if (publication?.status !== 'ready') return true
  const generationId = plan?.generation_id
  return publication?.generationId !== generationId
    && publication?.candidateGenerationId !== generationId
}
