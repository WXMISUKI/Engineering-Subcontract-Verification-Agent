import { describe, expect, it } from 'vitest'
import { partitionSelectedFiles } from '../src/plus-ui/blockchain-supervision/lib/uploadFiles'

describe('partitionSelectedFiles', () => {
  it('保留非空文件，并把空文件单独交给错误提示层', () => {
    const empty = { name: 'empty.pdf', size: 0 }
    const valid = { name: 'valid.pdf', size: 128 }

    expect(partitionSelectedFiles([empty, valid])).toEqual({
      files: [valid],
      emptyFiles: [empty],
    })
  })

  it('没有选择文件时返回两个空数组', () => {
    expect(partitionSelectedFiles(null)).toEqual({ files: [], emptyFiles: [] })
  })
})
