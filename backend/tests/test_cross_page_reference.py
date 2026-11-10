from app.plus.blockchain_supervision.cross_page_reference import match_ordered_page_fragments


def test_ordered_fragments_ignore_markdown_emphasis_in_ocr_text():
    text = "**第十五条** 禁止将承包的公路工程进行转包。\n有下列情形之一的，属于转包："
    report = {
        "status": "ok",
        "scope_complete": True,
        "offset": 0,
        "done": True,
        "text": text,
        "assembly_trace": {
            "complete": True,
            "coordinate_space": "full_document",
            "segments": [{
                "chunk_id": "ocr-7",
                "container_chunk_id": "text-7",
                "chunk_type": "image_ocr",
                "page_hint": 7,
                "page_hint_source": "image_placeholder_label",
                "start": 0,
                "end": len(text),
            }],
        },
    }

    parts = match_ordered_page_fragments(report, [{
        "page": 7,
        "role": "preamble",
        "text": "第十五条禁止将承包的公路工程进行转包。\n有下列情形之一的，属于转包：",
    }])

    assert parts == [{
        "role": "preamble",
        "page_hint": 7,
        "chunk_id": "ocr-7",
        "container_chunk_id": "text-7",
        "quote": text[2:],
    }]
