import hashlib
from types import SimpleNamespace

import pytest

from app.services.knowledge.fulltext import (
    assemble_document,
    document_from_report,
    trace_document_assembly,
)
from app.plus.blockchain_supervision import knowledge_runs
from app.services.knowledge.multi_kb_query import MultiKbQueryResult


def test_document_from_report_accepts_complete_text_and_ocr_trace():
    report = {
        "status": "ok",
        "scope_complete": True,
        "text": "前言\n条款正文",
        "assembly_trace": {
            "complete": True,
            "coordinate_space": "full_document",
            "segments": [
                {"chunk_id": "text-1", "start": 0, "end": 3},
                {"chunk_id": "ocr-1", "start": 3, "end": 7},
            ],
        },
    }

    assert document_from_report(report) == {
        "text": "前言\n条款正文",
        "segments": report["assembly_trace"]["segments"],
    }


def test_document_from_report_rejects_image_placeholder_without_ocr_trace():
    report = {
        "status": "ok",
        "scope_complete": True,
        "text": "![page_1.jpg](resource://page-1)",
        "assembly_trace": {
            "complete": False,
            "coordinate_space": "full_document",
            "segments": [],
        },
    }

    assert document_from_report(report) is None


def test_document_from_report_preserves_ocr_chunk_segments():
    text_chunks = [{
        "id": "text-1",
        "chunk_type": "text",
        "chunk_index": 0,
        "content": "![document_page_1.jpg](resource://page-1)",
    }]
    ocr_chunks = [{
        "id": "ocr-1",
        "chunk_type": "image_ocr",
        "content": "条款正文",
        "image_info": [{"url": "resource://page-1"}],
    }]
    assembled = assemble_document(text_chunks, ocr_chunks)
    report = {
        "status": "ok",
        "scope_complete": True,
        "text": assembled["text"],
        "assembly_trace": trace_document_assembly(text_chunks, ocr_chunks),
    }

    document = document_from_report(report)
    assert document is not None
    assert document["text"] == "条款正文"
    assert document["segments"] == [{
        "chunk_id": "ocr-1",
        "chunk_type": "image_ocr",
        "container_chunk_id": "text-1",
        "start": 0,
        "end": 4,
        "page_hint": 1,
        "page_hint_source": "image_placeholder_label",
    }]


def test_reference_fetch_uses_assembled_ocr_report(monkeypatch):
    text_chunks = [{
        "id": "text-1",
        "chunk_type": "text",
        "chunk_index": 0,
        "knowledge_id": "doc",
        "content": "适用范围\n![document_page_1.jpg](resource://page-1)",
    }]
    ocr_chunks = [{
        "id": "ocr-1",
        "chunk_type": "image_ocr",
        "knowledge_id": "doc",
        "content": "条款正文",
        "image_info": [{"url": "resource://page-1"}],
    }]
    assembled = assemble_document(text_chunks, ocr_chunks)
    report = {
        "status": "ok",
        "scope_complete": True,
        "text": assembled["text"],
        "chunks": [*text_chunks, *ocr_chunks],
        "assembly_trace": trace_document_assembly(text_chunks, ocr_chunks),
    }

    class Backend:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def fetch_document_text_reported(self, *_args, **_kwargs):
            return report

    monkeypatch.setattr(knowledge_runs, "_cross_page_connection", lambda *_args, **_kwargs: "connection")
    monkeypatch.setattr(knowledge_runs, "acquire_provider_backend",
                        lambda *_args, **_kwargs: Backend())
    monkeypatch.setattr(knowledge_runs, "_verify_authorized_original", lambda *_args, **_kwargs: True)

    parts = knowledge_runs._fetch_verified_text_parts(
        None,
        None,
        {"provider_id": 1, "binding_id": 2},
        {"kb_id": "kb", "knowledge_id": "doc"},
        {"text": "条款正文"},
        "适用范围",
        9999999999.0,
    )

    assert parts == [{
        "role": "preamble",
        "chunk_id": "text-1",
        "chunk_ids": ["text-1"],
        "quote": "适用范围",
    }, {
        "role": "item",
        "chunk_id": "ocr-1",
        "chunk_ids": ["ocr-1"],
        "quote": "条款正文",
    }]


@pytest.mark.parametrize('fault', [None, 'unauthorized', 'different_file', 'original_changed'])
def test_retrospective_shanghai_recovers_exact_ocr_chunks_without_repeated_document_reads(monkeypatch, fault):
    scope = {'provider_id': 1, 'binding_id': 2, 'kb_id': 'kb', 'knowledge_ids': ['shanghai']}
    query = MultiKbQueryResult('hit', [], [], ['kb'], None, total_targets=1, target_scopes=[scope])
    chunks = [
        {'id': 'ocr-1', 'knowledge_id': 'shanghai', 'kb_id': 'kb', 'chunk_type': 'image_ocr',
         'content': '第八条 承包人应核对分包范围。'},
        {'id': 'ocr-2', 'knowledge_id': 'shanghai', 'kb_id': 'kb', 'chunk_type': 'image_ocr',
         'content': '第九条 分包应满足适用的范围要求。'},
    ]
    if fault == 'different_file':
        chunks[1]['knowledge_id'] = 'unrelated-file'
    read_calls = []
    verify_calls = []

    class Backend:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def fetch_document_text_reported(self, knowledge_id, *, kb_id, max_chars):
            read_calls.append((knowledge_id, kb_id, max_chars))
            return {'status': 'ok', 'scope_complete': True, 'chunks': chunks}

    def clause(source, **_kwargs):
        return {'title': '上海公路工程施工分包细则'}, {
            'clause_id': source['clause_id'], 'text': source['expected_context']}

    original = b'pdf original'

    def read_original(*_args, **_kwargs):
        verify_calls.append(True)
        return b'different pdf' if fault == 'original_changed' else original

    monkeypatch.setattr(knowledge_runs, 'query_subcontract_knowledge', lambda *_args, **_kwargs: query)
    monkeypatch.setattr(knowledge_runs, '_clause', clause)
    monkeypatch.setattr(knowledge_runs, '_cross_page_connection',
                        lambda *_args: None if fault == 'unauthorized' else 'connection')
    monkeypatch.setattr(knowledge_runs, 'acquire_provider_backend', lambda *_args, **_kwargs: Backend())
    monkeypatch.setattr(knowledge_runs, 'read_authorized_reference_original', read_original)
    sources = [{
        'doc_id': 'SHJT-2026-8', 'clause_id': 'SHJT-2026-8#' + article,
        'query_text': '上海公路工程施工分包', 'kb_id': 'kb', 'knowledge_id': 'shanghai',
        'original_sha256': hashlib.sha256(original).hexdigest(), 'expected_context': chunk['content'],
        'retrospective_shanghai_lookup': True,
    } for article, chunk in zip(('第八条', '第九条'), chunks)]
    task = SimpleNamespace(conversation_id=5, created_by=6, created_by_type='user')
    result = knowledge_runs.retrieve_reference_run(None, task, {'basis_sources': sources}, as_of=None)
    if fault:
        assert result['status'] == 'undetermined' and result['references'] == []
        assert len(read_calls) == (0 if fault == 'unauthorized' else 1)
        assert len(verify_calls) == (1 if fault == 'original_changed' else 0)
        return
    assert result['status'] == 'ok'
    assert [ref['chunk_id'] for ref in result['references']] == ['ocr-1', 'ocr-2']
    assert all(ref['purpose'] == 'reference_only' and not ref['used_for_decision']
               and ref['physical_page'] is None and ref['lookup_method'] == 'authorized_document_chunks'
               for ref in result['references'])
    assert read_calls == [('shanghai', 'kb', None)]
    assert len(verify_calls) == 1


@pytest.mark.parametrize('revoke_before_second_clause', [False, True])
def test_exact_and_section_references_share_same_document_and_original(monkeypatch, revoke_before_second_clause):
    scope = {'provider_id': 1, 'binding_id': 2, 'kb_id': 'kb', 'knowledge_ids': ['shanghai']}
    query = MultiKbQueryResult('hit', [], [], ['kb'], None, total_targets=1, target_scopes=[scope])
    chunks = [{'id': 'text-1', 'knowledge_id': 'shanghai', 'kb_id': 'kb', 'chunk_type': 'text',
               'chunk_index': 0, 'content': '第八条 范围义务。\n本节规则\n第一项要求。\n第二项要求。'}]
    original = b'pdf original'
    document_reads = []
    original_reads = []
    authorized = True

    class Backend:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def fetch_document_text_reported(self, knowledge_id, *, kb_id, max_chars):
            document_reads.append((knowledge_id, kb_id))
            return {'status': 'ok', 'scope_complete': True, 'chunks': chunks,
                    'text': chunks[0]['content'], 'assembly_trace': trace_document_assembly(chunks, [])}

    def clause(source, **_kwargs):
        nonlocal authorized
        clause_id = source['clause_id']
        if source.get('section_item_anchor'):
            authorized = not revoke_before_second_clause
            return {'title': '上海细则'}, {'clause_id': clause_id, 'text': '本节规则第二项要求。',
                                          'item_line': '第二项要求。', 'section_anchor': '本节规则'}
        return {'title': '上海细则'}, {'clause_id': clause_id, 'text': '第八条 范围义务。'}

    def read_original(*_args, **_kwargs):
        original_reads.append(True)
        return original

    monkeypatch.setattr(knowledge_runs, 'query_subcontract_knowledge', lambda *_args, **_kwargs: query)
    monkeypatch.setattr(knowledge_runs, '_clause', clause)
    monkeypatch.setattr(knowledge_runs, '_cross_page_connection', lambda *_args: 'connection' if authorized else None)
    monkeypatch.setattr(knowledge_runs, 'acquire_provider_backend', lambda *_args, **_kwargs: Backend())
    monkeypatch.setattr(knowledge_runs, 'read_authorized_reference_original', read_original)
    source = {'doc_id': 'SHJT-2026-8', 'query_text': '上海公路工程施工分包', 'kb_id': 'kb',
              'knowledge_id': 'shanghai', 'original_sha256': hashlib.sha256(original).hexdigest(),
              'retrospective_shanghai_lookup': True}
    sources = [{**source, 'clause_id': 'SHJT-2026-8#第八条'},
               {**source, 'clause_id': 'SHJT-2026-8#第二项', 'section_item_anchor': True}]
    task = SimpleNamespace(conversation_id=5, created_by=6, created_by_type='user')

    result = knowledge_runs.retrieve_reference_run(None, task, {'basis_sources': sources}, as_of=None)

    expected = sources[:1] if revoke_before_second_clause else sources
    assert [ref['clause_id'] for ref in result['references']] == [s['clause_id'] for s in expected]
    assert document_reads == [('shanghai', 'kb')]
    assert original_reads == [True]
