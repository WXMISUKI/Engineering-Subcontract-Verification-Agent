import { describe, expect, it } from 'vitest'
import { buildPersonnelClueGroups } from '../src/plus-ui/blockchain-supervision/lib/personnelClues'

describe('buildPersonnelClueGroups', () => {
  it('按身份键隔离同名人员，同人同页两张证书保留各自字段', () => {
    const groups = buildPersonnelClueGroups([
      { evidence_kind: 'management_roster', name: '张三', person_key: 'id:a', material_id: 1, page: 1,
        certificate_identity_state: 'identity_fields_correspond', professional_title: '工程师' },
      ...['B123', 'R456'].map((number) => ({ evidence_kind: 'person_certificate', name: '张三',
        person_key: 'id:a', material_id: 2, page: 1, certificate_number: number, quote: number })),
      { evidence_kind: 'person_certificate', name: '张三', person_key: 'id:b', material_id: 2, page: 1, quote: 'C789' },
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0].certificateView).toBe(true)
    expect(groups[0].sides.certificate.map((row) => row.fields)).toEqual([['编号：B123'], ['编号：R456']])
    expect(groups[0].sides.roster[0].fields).toContain('职称：工程师')
    expect(groups[1].sides.certificate).toHaveLength(1)
    expect(groups[0].key).not.toBe(groups[1].key)
  })
  it('同页证照无唯一字段时显示标题、冲突候选和身份待核实', () => {
    const [group] = buildPersonnelClueGroups([
      { evidence_kind: 'person_certificate', name: '张三', material_id: 1, page: 2,
        document_title: '特种作业操作证', certificate_number_candidates: ['B123', 'B999'],
        certificate_identity_state: 'identity_pending' },
    ])
    expect(group.certificateView).toBe(true)
    expect(group.sides.certificate[0].fields).toEqual([
      '特种作业操作证', '编号候选：B123 / B999（需核实）', '身份待核实',
    ])
  })
  it('按主体和人员分组，不把同名跨主体合并', () => {
    const groups = buildPersonnelClueGroups([
      { evidence_kind: 'management_roster', name: '张三', post: '项目负责人', subject_id: 1, material_id: 10, page: 17 },
      { evidence_kind: 'social_record', name: '张三', subject_id: 1, material_id: 11, page: 20 },
      { evidence_kind: 'management_roster', name: '张三', post: '安全员', subject_id: 2, material_id: 30, page: 5 },
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0].sides.roster[0].page).toBe(17)
    expect(groups[0].sides.social[0].page).toBe(20)
    expect(groups[1].sides.roster[0].page).toBe(5)
  })

  it('同一材料同一页只保留一条，并忽略无页码引用', () => {
    const groups = buildPersonnelClueGroups([
      { evidence_kind: 'labor_contract', name: '李四', post: '劳务员', material_id: 12, page: 30 },
      { evidence_kind: 'labor_contract', name: '李四', material_id: 12, page: 30 },
      { evidence_kind: 'social_record', name: '李四', material_id: 12, page: null },
      { evidence_kind: 'other', name: '王五', material_id: 12, page: 1 },
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].sides.labor).toEqual([{ materialId: 12, page: 30, quote: '' }])
    expect(groups[0].sides.social).toEqual([])
  })
})
