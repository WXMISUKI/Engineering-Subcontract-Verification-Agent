import { describe, expect, it } from 'vitest'
import { zipPreparationFailureMessages } from '../src/plus-ui/blockchain-supervision/lib/materialState'
import { buildPrototypeTask, buildUploadReadiness } from '../src/plus-ui/blockchain-supervision/prototype/prototypeViewModel'

describe('ZIP 准备失败展示数据', () => {
  it('保留后端的准备失败原因，供上传页显示', () => {
    const task = buildPrototypeTask({
      task_id: 9,
      zip_preparation: {
        state: 'failed',
        containers: [{ file_name: 'danger.zip', message: '资料包内含上级目录条目（..），无法安全展开' }],
      },
    })

    expect(zipPreparationFailureMessages(task.zipPreparation)).toEqual([
      '资料包内含上级目录条目（..），无法安全展开',
    ])
  })

  it('准备失败时阻断开始核查', () => {
    const readiness = buildUploadReadiness({
      validation: { hard_ok: true },
      materials: [{ id: 1 }],
      zipPreparation: {
        state: 'failed',
        containers: [{ file_name: 'danger.zip', message: '资料包无法安全展开' }],
      },
    })

    expect(readiness.ready).toBe(false)
    expect(readiness.reasons).toContain('资料包准备失败，请移除失败资料包后重新上传')
  })

  it('保留解析失败替换候选的失败原因和旧材料替代目标', () => {
    const task = buildPrototypeTask({
      task_id: 10,
      materials: [{
        id: 22,
        file_name: 'corrupt.pdf',
        parse_state: 'failed',
        replacement_target_id: 21,
        degradations: [{ kind: 'parse_failed', message: '材料无法读取，请检查文件是否损坏' }],
      }],
    })

    expect(task.materials[0].replacementTargetId).toBe(21)
    expect(task.materials[0].parseFailureMessage).toBe('材料无法读取，请检查文件是否损坏')
  })

  it('不把容器路径或底层异常直接展示给用户', () => {
    const task = buildPrototypeTask({
      materials: [{
        id: 23,
        file_name: 'corrupt.pdf',
        parse_state: 'failed',
        degradations: [{ kind: 'parse_failed', message: "Failed to open file '/app/app/uploads/bs/43/corrupt.pdf' as type pdf." }],
      }],
    })

    expect(task.materials[0].parseFailureMessage).toBe('材料无法读取，请检查文件是否损坏')
  })
})
