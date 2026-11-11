from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.plus.blockchain_supervision.models import BsMaterial, BsReviewVersion, BsTask
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def _client(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "report-version.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    create_account(engine, "bob", "Bob-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    task = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation_id,
        "title": "历史版本 HTTP 验证",
        "scope_a1": True,
    })
    assert task.status_code == 200, task.text
    return client, engine, task.json()["task_id"]


def _seed_version(engine, task_id):
    with Session(engine) as db:
        material = BsMaterial(
            task_id=task_id,
            file_name="历史材料.pdf",
            role="packet",
            parse_state="done",
            pages=1,
            pages_json=[{"page": 1, "text": "当前材料文字"}],
        )
        db.add(material)
        db.flush()
        db.add(BsReviewVersion(
            task_id=task_id,
            version_no=1,
            trigger="completed",
            snapshot_json={
                "report": {"business_document": {"basic_information": {"工程名称": "历史工程"}}},
                "findings": [],
                "reference_snapshot": {"review_run_id": "review-history-1", "runs": [], "steps": []},
                "frozen_material_pages": {
                    str(material.id): {
                        "1": {"material_id": material.id, "page": 1,
                              "text": "历史冻结页文字", "readable": True},
                    },
                },
            },
        ))
        db.commit()
        return material.id


def test_history_version_and_page_are_read_only_and_cross_user_is_hidden(tmp_path):
    client, engine, task_id = _client(tmp_path)
    material_id = _seed_version(engine, task_id)

    versions = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/versions")
    assert versions.status_code == 200
    assert [row["version_no"] for row in versions.json()["versions"]] == [1]

    version = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/versions/1")
    assert version.status_code == 200
    assert version.json()["snapshot"]["report"]["business_document"]["basic_information"]["工程名称"] == "历史工程"

    historical_report = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/report",
        params={"version_no": 1},
    )
    assert historical_report.status_code == 200
    assert historical_report.json()["business_document"]["basic_information"]["工程名称"] == "历史工程"

    page = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/versions/1/materials/{material_id}/pages/1"
    )
    assert page.status_code == 200
    assert page.json()["text"] == "历史冻结页文字"

    missing_page = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/versions/1/materials/{material_id}/pages/2"
    )
    assert missing_page.status_code == 404

    assert client.post("/api/login", json={"username": "bob", "password": "Bob-pass-123"}).status_code == 200
    hidden = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/versions")
    assert hidden.status_code in (403, 404)
    hidden_report = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/report",
        params={"version_no": 1},
    )
    assert hidden_report.status_code in (403, 404)


def test_version_diff_blocks_current_side_while_supplement_is_active(tmp_path):
    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)

    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {
            "supplement_plan": {"generation_id": "gen-1", "status": "planned"},
            "supplement_edit": {"generation_id": "gen-1"},
        }
        db.commit()

    blocked = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/versions/diff",
        params={"a": "1", "b": "current"},
    )
    assert blocked.status_code == 409
    assert "补件核查尚未完成" in blocked.json()["detail"]

    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {
            "supplement_plan": {"generation_id": "gen-1", "status": "completed"},
            "supplement_edit": {"generation_id": "gen-1"},
        }
        db.commit()

    diff = client.get(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/versions/diff",
        params={"a": "1", "b": "current"},
    )
    assert diff.status_code == 200, diff.text
    assert diff.json()["a"]["version_no"] == 1
    assert diff.json()["b"]["version_no"] is None


def test_historical_pdf_export_preserves_frozen_summary_and_access(tmp_path):
    import copy
    from io import BytesIO
    import fitz
    from docx import Document

    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)
    with Session(engine) as db:
        version = db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one()
        snapshot = copy.deepcopy(version.snapshot_json)
        snapshot["report"] = {
            "meta": {"title": "历史工程", "scope": {}},
            "business_document": {
                "basic_information": {"工程名称": "历史工程"},
                "sections": [],
                "issues": [
                    {"category": "人员核查", "label": "旧事项", "summary_theme": "人员资料", "status": "undecidable"},
                    {"category": "人员核查", "label": "人工意见", "status": "hit",
                     "human_disposition": {"code": "accepted", "note": "历史备注"}},
                    {"category": "人员核查", "label": "不显示的忽略项", "status": "hit",
                     "human_disposition": {"code": "ignored"}},
                ],
                "conclusion": {"text": "尚待核实", "limitations": []},
            },
        }
        version.snapshot_json = snapshot
        db.commit()

    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export",
                          params={"fmt": "pdf", "version_no": 1})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-")
    pdf = fitz.open(stream=response.content, filetype="pdf")
    text = "".join(page.get_text() for page in pdf)
    assert "历史工程" in text and "人员资料需要核实" in text and "历史备注" in text
    assert "不显示的忽略项" not in text
    assert all(pdf.extract_font(font[0])[3] for page in pdf for font in page.get_fonts())
    word_response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export",
                               params={"fmt": "docx", "version_no": 1})
    assert word_response.status_code == 200
    word = Document(BytesIO(word_response.content))
    word_text = "\n".join([p.text for p in word.paragraphs] +
                          [cell.text for table in word.tables for row in table.rows for cell in row.cells])
    assert "历史工程" in word_text and "人员资料需要核实" in word_text and "历史备注" in word_text
    assert "不显示的忽略项" not in word_text
    with Session(engine) as db:
        saved = db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one()
        assert saved.snapshot_json == snapshot

    assert client.post("/api/login", json={"username": "bob", "password": "Bob-pass-123"}).status_code == 200
    hidden = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export",
                        params={"fmt": "pdf", "version_no": 1})
    assert hidden.status_code in (403, 404)


def test_report_recipient_confirmation_is_task_scoped_and_invalidates_on_material_change(tmp_path):
    from app.plus.blockchain_supervision import reporting

    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)
    with Session(engine) as db:
        m = BsMaterial(task_id=task_id, file_name="报审表.pdf", role="packet", parse_state="done",
                       pages=1, pages_json=[{"page": 1, "text": "劳务分包报审表\n致：上海沪申高速公路建设发展有限公司"}])
        db.add(m)
        db.commit()
        material_id = m.id
    before = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}").json()["contract_party_evidence"]["employer"]
    assert before["company"] is None and before["candidate_company"] == "上海沪申高速公路建设发展有限公司"
    response = client.post(f"/api/plus/blockchain-supervision/tasks/{task_id}/employer-confirmation",
                           json={"material_id": material_id, "page": 1, "value": "上海沪申高速公路建设发展有限公司"})
    assert response.status_code == 200, response.text
    after = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}").json()["contract_party_evidence"]["employer"]
    assert after["company"] == "上海沪申高速公路建设发展有限公司" and after["source"] == "human_confirmation"
    historical = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report", params={"version_no": 1})
    assert historical.status_code == 200
    assert "建设单位（业主）" not in historical.json()["business_document"]["basic_information"]
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        assert reporting.build_report(db, task)["business_document"]["basic_information"]["建设单位（业主）"] == after["company"]
        m = db.get(BsMaterial, material_id)
        m.pages_json = [{"page": 1, "text": "劳务分包报审表\n致：另一接收方有限公司"}]
        db.commit()
    current = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}").json()["contract_party_evidence"]["employer"]
    assert current["company"] is None and current["candidate_company"] == "另一接收方有限公司"
    stale = client.post(f"/api/plus/blockchain-supervision/tasks/{task_id}/employer-confirmation",
                        json={"material_id": material_id, "page": 1, "value": "上海沪申高速公路建设发展有限公司"})
    assert stale.status_code == 409
    assert client.post("/api/login", json={"username": "bob", "password": "Bob-pass-123"}).status_code == 200
    hidden = client.post(f"/api/plus/blockchain-supervision/tasks/{task_id}/employer-confirmation",
                         json={"material_id": material_id, "page": 1, "value": "另一接收方有限公司"})
    assert hidden.status_code in (403, 404)
