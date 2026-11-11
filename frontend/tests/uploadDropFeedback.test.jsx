import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import PrototypeUploadPage from '../src/plus-ui/blockchain-supervision/prototype/PrototypeUploadPage'
import { validateUploadFiles } from '../src/plus-ui/blockchain-supervision/lib/uploadFiles'

afterEach(cleanup)

it('拖入多个 PDF/ZIP 交给同一上传处理函数', () => {
  const onDropFiles = vi.fn()
  render(<PrototypeUploadPage task={{ materials: [] }} onDropFiles={onDropFiles} />)
  const files = [new File(['a'], 'one.PDF'), new File(['b'], 'two.zip')]
  fireEvent.dragOver(screen.getByTestId('upload-drop-zone'), { dataTransfer: { files } })
  fireEvent.drop(screen.getByTestId('upload-drop-zone'), { dataTransfer: { files } })
  expect(onDropFiles).toHaveBeenCalledTimes(1)
  expect(Array.from(onDropFiles.mock.calls[0][0])).toEqual(files)
})

it('忙碌或只读状态下拖入不提交文件', () => {
  const onDropFiles = vi.fn()
  const view = render(<PrototypeUploadPage busy task={{ materials: [] }} onDropFiles={onDropFiles} />)
  fireEvent.drop(screen.getByTestId('prototype-upload-page'), { dataTransfer: { files: [new File(['x'], 'one.pdf')] } })
  expect(onDropFiles).not.toHaveBeenCalled()
  view.rerender(<PrototypeUploadPage readOnly task={{ materials: [] }} onDropFiles={onDropFiles} />)
  fireEvent.drop(screen.getByTestId('prototype-upload-page'), { dataTransfer: { files: [new File(['x'], 'one.pdf')] } })
  expect(onDropFiles).not.toHaveBeenCalled()
})

it('文件选择与拖入共享格式和空文件分类', () => {
  const pdf = new File(['a'], 'one.pdf')
  const bad = new File(['x'], 'photo.png')
  const empty = new File([], 'empty.pdf')
  const result = validateUploadFiles([pdf, bad, empty])
  expect(result.files).toEqual([pdf])
  expect(result.unsupportedFiles).toEqual([bad])
  expect(result.emptyFiles).toEqual([empty])
})
