from pathlib import Path


def test_backend_dockerfile_normalizes_requirements_line_endings_before_pdf_split():
    dockerfile = (Path(__file__).resolve().parents[2] / "docker" / "Dockerfile.backend").read_text(
        encoding="utf-8"
    )

    assert "tr -d '\\r' < /app/requirements.txt" in dockerfile
    assert "/tmp/requirements-normalized.txt" in dockerfile
    assert "grep -cx 'pymupdf==1.28.0' /tmp/requirements-normalized.txt" in dockerfile
