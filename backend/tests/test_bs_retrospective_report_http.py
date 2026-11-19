from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.models import BsReviewVersion, BsTask
from test_bs_report_version_http import _client


def _stored_report(state):
    return {"review_mode": "contract_retrospective", "execution_status": state,
        "identity": {"run_id": "retro-test"}, "summary": "已检查范围内未发现可列示问题。",
        "scope_note": "单合同范围", "limitations": [], "rows": [],
        "template_document": {"template_id": "contract-retrospective.v1", "title": "分包合同文本倒查报告",
            "page_layout": "landscape", "blocks": [
                {"type": "paragraph", "text": "范围说明"},
                {"type": "table", "headers": ["项目名称", "总包/分包", "合同文件", "条款位置",
                    "合同摘录", "风险说明", "依据文件", "依据条款"], "rows": [],
                    "widths": [.07, .05, .1, .1, .2, .22, .1, .16]},
            ]}}


def test_saved_retrospective_report_is_read_and_exported_without_admission_gate(tmp_path):
    client, engine, task_id = _client(tmp_path)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {"review_mode": "contract_retrospective", "retrospective_run_id": "retro-test"}
        task.state = "done"
        db.add(BsReviewVersion(task_id=task_id, version_no=1, trigger="completed",
            snapshot_json={"retrospective_report": _stored_report("completed")}))
        db.commit()

    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report")
    assert response.status_code == 200
    assert response.json()["template_document"]["template_id"] == "contract-retrospective.v1"
    exported = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export?fmt=docx")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"


def test_failed_retrospective_report_cannot_be_exported_as_findings(tmp_path):
    client, engine, task_id = _client(tmp_path)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {"review_mode": "contract_retrospective", "retrospective_run_id": "retro-test"}
        task.state = "failed"
        db.add(BsReviewVersion(task_id=task_id, version_no=1, trigger="completed",
            snapshot_json={"retrospective_report": _stored_report("failed")}))
        db.commit()
    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export?fmt=pdf")
    assert response.status_code == 409
