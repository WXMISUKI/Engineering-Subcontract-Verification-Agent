import { expect, it } from 'vitest'
import BlockchainSupervisionWorkbench from '../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench'

it('loads the workbench with its report confirmation dependencies', () => {
  expect(typeof BlockchainSupervisionWorkbench).toBe('function')
})
