"""Project attributable unread pages for unassessed negative-list candidates."""
import re


def project(candidates, run, materials, coverage_by_id):
    if not isinstance(candidates, list) or not isinstance(run, dict):
        return None
    by_id = {m.id: m for m in materials}
    result = {}
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        if not (candidate.get('execution_status') == 'not_run'
                and candidate.get('unavailable_reason') == 'rule_signal_not_found'
                and candidate.get('source_reading_complete') is False):
            continue
        candidate_id, rule_id = candidate.get('candidate_id'), candidate.get('item_id')
        if (not isinstance(candidate_id, str) or not candidate_id.startswith('nc-') or candidate_id in seen
                or not isinstance(rule_id, str) or not re.fullmatch(r'NL-[BT]-\d{2}', rule_id)
                or any(not run.get(key) or candidate.get(key) != run.get(key) for key in
                       ('task_id', 'run_id', 'review_run_id', 'material_fingerprint', 'rule_fingerprint'))):
            return None
        seen.add(candidate_id)
        ids = candidate.get('contract_ids')
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids):
            return None
        gaps = 0
        for material_id in ids:
            material = by_id.get(material_id)
            coverage = coverage_by_id.get(material_id)
            if (material is None or material.task_id != run['task_id'] or material.subject_id != candidate.get('subject_id')
                    or material.superseded_by_id is not None or not isinstance(coverage, dict)):
                return None
            if coverage.get('reading_complete') is True:
                continue
            total, pages = coverage.get('total_pages'), coverage.get('unread_pages')
            if (type(total) is not int or total < 1 or not isinstance(pages, list) or not pages
                    or any(type(p) is not int or p < 1 or p > total for p in pages)):
                return None
            gaps += 1
            item = result.setdefault(material_id, {'material_id': material_id, 'file_name': material.file_name,
                                                   'pages': set(), 'rule_ids': set()})
            item['pages'].update(pages)
            item['rule_ids'].add(rule_id)
        if not gaps:
            return None
    return [{**row, 'pages': sorted(row['pages']), 'rule_ids': sorted(row['rule_ids'])}
            for _, row in sorted(result.items())]


def collect(db, task, snapshot, coverage):
    from types import SimpleNamespace
    from . import ai_advisory_snapshots, knowledge_runs, material_versions, parsing
    from .ai_current_guard import assert_ai_context_current
    from .models import BsMaterial, BsTaskEvent

    groups = (coverage or {}).get('groups') or []
    a1 = next((row for row in groups if row.get('group_id') == 'a1'), None)
    expected = (a1 or {}).get('reading_gap_not_run', 0)
    if type(expected) is not int or expected <= 0:
        return None
    run = knowledge_runs.get_reference_run(db, task, group_id='a1')
    if not run or not snapshot or run.get('review_run_id') != snapshot.get('review_run_id'):
        return None
    try:
        assert_ai_context_current(db, task, run, SimpleNamespace(id=task.created_by, user_type=task.created_by_type),
                                  allow_completed=True, require_references=False)
    except Exception:
        return None
    events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
              BsTaskEvent.event_type.in_(('check.negative_candidates_started', 'check.negative_candidates_finished',
                                          'check.negative_candidates_failed')))
              .order_by(BsTaskEvent.sequence.desc()).all())
    event = next((e for e in events if (e.payload_json or {}).get('run_id') == run.get('run_id')), None)
    if not event or event.event_type != 'check.negative_candidates_finished' or not ai_advisory_snapshots.advisory_validation_current(event):
        return None
    result = (event.payload_json or {}).get('result') or {}
    candidates = result.get('candidates')
    if not isinstance(candidates, list):
        return None
    gaps = [row for row in candidates if isinstance(row, dict) and row.get('execution_status') == 'not_run'
            and row.get('unavailable_reason') == 'rule_signal_not_found' and row.get('source_reading_complete') is False]
    if len(gaps) != expected:
        return None
    materials = material_versions.active_input_materials(db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all())
    return project(gaps, run, materials, {material.id: parsing.reading_coverage(material) for material in materials})


def statements(rows):
    return ['负面清单 AI 辅助候选未覆盖规则 %s；材料「%s」第 %s 页识别不完整，相关规则未形成符合或不符合结论。'
            % ('、'.join(row['rule_ids']), row['file_name'], '、'.join(str(page) for page in row['pages']))
            for row in rows]
