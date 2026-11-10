from app.services.knowledge.fulltext import (
    assemble_document,
    document_from_report,
    trace_document_assembly,
)
from app.plus.blockchain_supervision import knowledge_runs


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
