import { describe, expect, it } from 'vitest'
import { supplementPublicationPending } from '../src/plus-ui/blockchain-supervision/lib/supplementPublication'

describe('supplement publication readiness', () => {
  it('waits while a running candidate has no ready publication payload', () => {
    expect(supplementPublicationPending({
      plan: { status: 'running', generation_id: 'candidate-2' },
      publication: { status: 'preparing', generationId: 'candidate-1' },
    })).toBe(true)
  })

  it('accepts a ready candidate generation even when published points to the prior version', () => {
    expect(supplementPublicationPending({
      plan: { status: 'running', generation_id: 'candidate-2' },
      publication: {
        status: 'ready',
        generationId: 'candidate-1',
        candidateGenerationId: 'candidate-2',
      },
    })).toBe(false)
  })

  it('does not gate non-running plans', () => {
    expect(supplementPublicationPending({
      plan: { status: 'ready', generation_id: 'candidate-2' },
      publication: null,
    })).toBe(false)
  })
})
