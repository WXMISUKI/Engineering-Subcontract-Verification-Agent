import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import PersonnelEvidenceClues from '../src/plus-ui/blockchain-supervision/components/PersonnelEvidenceClues'

describe('人员证照候选呈现', () => {
  it('合同人员表仅显示名单和资格声明，不作为已提供证书', () => {
    const html = renderToStaticMarkup(<PersonnelEvidenceClues evidence={[
      { evidence_kind: 'contract_roster', name: '张三', post: '项目经理', material_id: 1, page: 45,
        qualification_declaration: '二级建造师', certificate_identity_state: 'identity_pending' },
    ]} />)
    expect(html).toContain('名单声明')
    expect(html).toContain('合同资格声明：二级建造师')
    expect(html).toContain('身份待核实')
    expect(html).toContain('证照字段候选：未定位')
  })
  it('分别显示实际记录候选与承诺，不宣称已到岗', () => {
    const html = renderToStaticMarkup(<PersonnelEvidenceClues evidence={[
      { evidence_kind: 'attendance_record', name: '张三', material_id: 1, page: 1,
        project: '甲工程', period: '2026-11-01', attendance_state: '请假', attendance_identity_state: 'identity_pending' },
      { evidence_kind: 'attendance_commitment', name: '张三', material_id: 2, page: 2,
        attendance_identity_state: 'identity_pending' },
    ]} />)
    expect(html).toContain('考勤记录候选')
    expect(html).toContain('原文状态：请假')
    expect(html).toContain('到岗承诺（仅声明）')
    expect(html).toContain('甲工程')
    expect(html).not.toContain('社保：未定位')
  })
  it('显示同页证书标题和冲突值，不把证书核对视图说成缺社保', () => {
    const html = renderToStaticMarkup(<PersonnelEvidenceClues evidence={[
      { evidence_kind: 'management_roster', name: '张三', person_key: 'id:a', material_id: 1, page: 1,
        certificate_identity_state: 'identity_pending' },
      { evidence_kind: 'person_certificate', name: '张三', person_key: 'name-candidate', material_id: 2, page: 2,
        document_title: '特种作业操作证', certificate_number_candidates: ['B123', 'B999'],
        certificate_identity_state: 'identity_pending' },
    ]} />)
    expect(html).toContain('特种作业操作证')
    expect(html).toContain('B123 / B999（需核实）')
    expect(html).toContain('身份待核实')
    expect(html).toContain('名单声明')
    expect(html).not.toContain('社保：未定位')
    expect(html).not.toContain('劳动合同：未定位')
  })
})
