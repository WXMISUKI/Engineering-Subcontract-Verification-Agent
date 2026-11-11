from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import service
from app.plus.blockchain_supervision.models import BsMaterial, BsTask, BsTaskEvent
from app.standalone.identity import Base


def test_finish_ingest_keeps_seal_progress_in_main_writer_session(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "ingest-lock.sqlite").as_posix(),
        connect_args={"check_same_thread": False, "timeout": 0.2},
    )
    Base.metadata.create_all(engine)
    source = tmp_path / "material.pdf"
    source.write_bytes(b"synthetic-pdf")
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="ingest-lock", created_by=1,
                      created_by_type="web", state="running", options_snapshot_json={}))
        db.add(BsMaterial(id=1, task_id=1, file_name="material.pdf", role="unknown",
                          parse_state="uploaded", path=str(source)))
        db.commit()

    writers = []

    def session_factory(*args, **kwargs):
        session = Session(bind=kwargs["bind"])
        writers.append(session)
        return session

    monkeypatch.setattr(service, "SessionLocal", session_factory)
    monkeypatch.setattr(service, "_material_sha256", lambda path: "sha")
    monkeypatch.setattr(service, "persist_material_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "resolve_task_relations", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "material_subject_conflict", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_activate_pending_replacement", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.parsing, "parse_material_pages",
                        lambda path, progress: ([{"page": 1, "text": "ok"}], []))

    service._finish_ingest(1, 1, engine)

    assert len(writers) == 1
    with Session(engine) as db:
        seal_events = db.query(BsTaskEvent).filter_by(
            task_id=1, event_type="material.page_progress").all()
        assert any((event.payload_json or {}).get("source") == "seal" for event in seal_events)
