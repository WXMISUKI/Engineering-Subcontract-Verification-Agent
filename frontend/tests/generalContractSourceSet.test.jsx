import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import PrototypeUploadPage from '../src/plus-ui/blockchain-supervision/prototype/PrototypeUploadPage'
import { buildPrototypeMaterial } from '../src/plus-ui/blockchain-supervision/prototype/prototypeViewModel'

afterEach(cleanup)

function material(id, overrides = {}) {
  return {
    id,
    fileName: `总包合同${id}.pdf`,
    role: 'general',
    parseState: 'done',
    ...overrides,
  }
}

function renderSourceSetPage({ materials, taskState = 'setup', readOnly = false, props = {} }) {
  return render(
    <PrototypeUploadPage
      task={{ state: taskState, materials, validation: { hard_ok: true } }}
      readOnly={readOnly}
      {...props}
    />,
  )
}

it('allows explicitly associating selected current general materials and excludes inactive rows', async () => {
  const onAssociateGeneralSourceSet = vi.fn(async () => true)
  renderSourceSetPage({
    materials: [
      material(1),
      material(2),
      material(3, { supersededById: 4 }),
      material(4, { replacementTargetId: 3 }),
      material(5, { subjectConflict: { reason: 'conflict' } }),
      material(6, { role: 'sub' }),
      material(7, { containerMaterialId: 10 }),
    ],
    props: { onAssociateGeneralSourceSet },
  })

  fireEvent.click(screen.getByLabelText('选择总包材料：总包合同1.pdf'))
  fireEvent.click(screen.getByLabelText('选择总包材料：总包合同2.pdf'))
  expect(screen.queryByLabelText('选择总包材料：总包合同3.pdf')).toBeNull()
  expect(screen.queryByLabelText('选择总包材料：总包合同4.pdf')).toBeNull()
  expect(screen.queryByLabelText('选择总包材料：总包合同5.pdf')).toBeNull()
  expect(screen.queryByLabelText('选择总包材料：总包合同6.pdf')).toBeNull()
  expect(screen.queryByLabelText('选择总包材料：总包合同7.pdf')).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: '确认关联为同一总包' }))
  await waitFor(() => expect(onAssociateGeneralSourceSet).toHaveBeenCalledWith([1, 2]))
})

it('preserves the ZIP container identity used to exclude non-rule materials from source linking', () => {
  expect(buildPrototypeMaterial({ material_id: 8, container_material_id: 7 }).containerMaterialId).toBe(7)
})

it('shows current source-set membership and allows removing one member in setup', async () => {
  const onRemoveGeneralSourceSetMember = vi.fn(async () => true)
  renderSourceSetPage({
    materials: [
      material(11, { generalSourceSetId: 'set-a', generalSourceSetVersion: 2 }),
      material(12, { generalSourceSetId: 'set-a', generalSourceSetVersion: 2 }),
      material(13),
    ],
    props: { onRemoveGeneralSourceSetMember },
  })

  expect(screen.getByTestId('general-source-set-set-a').textContent).toContain('总包合同11.pdf')
  expect(screen.getByTestId('general-source-set-set-a').textContent).toContain('总包合同12.pdf')
  fireEvent.click(screen.getByRole('button', { name: '从来源集中解除：总包合同11.pdf' }))
  await waitFor(() => expect(onRemoveGeneralSourceSetMember).toHaveBeenCalledWith(11))
})

it('requires all members of an existing set to be selected before extending or merging it', () => {
  renderSourceSetPage({
    materials: [
      material(21, { generalSourceSetId: 'set-b', generalSourceSetVersion: 1 }),
      material(22, { generalSourceSetId: 'set-b', generalSourceSetVersion: 1 }),
      material(23),
    ],
  })

  fireEvent.click(screen.getByLabelText('选择总包材料：总包合同21.pdf'))
  fireEvent.click(screen.getByLabelText('选择总包材料：总包合同23.pdf'))
  expect(screen.getByRole('button', { name: '确认关联为同一总包' }).disabled).toBe(true)
})

it('keeps source-set controls read-only after setup has ended', () => {
  const materials = [
    material(31, { generalSourceSetId: 'set-c', generalSourceSetVersion: 1 }),
    material(32),
  ]
  const { rerender } = renderSourceSetPage({ materials, readOnly: true })
  expect(screen.queryByLabelText('选择总包材料：总包合同32.pdf')).toBeNull()
  expect(screen.queryByRole('button', { name: '从来源集中解除：总包合同31.pdf' })).toBeNull()

  rerender(
    <PrototypeUploadPage task={{ state: 'running', materials, validation: { hard_ok: true } }} />,
  )
  expect(screen.queryByLabelText('选择总包材料：总包合同32.pdf')).toBeNull()
  expect(screen.queryByRole('button', { name: '从来源集中解除：总包合同31.pdf' })).toBeNull()
})
