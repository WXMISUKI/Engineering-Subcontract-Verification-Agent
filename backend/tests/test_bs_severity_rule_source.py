import json
from pathlib import Path
from types import SimpleNamespace

from app.plus.blockchain_supervision import rules, severity
from app.plus.blockchain_supervision.models import STATUS_HIT, STATUS_UNDECIDABLE
from app.plus.blockchain_supervision.severity_rules import load_severity_matrix


ROOT = Path(__file__).resolve().parents[1]


def _finding(message, status, *, quote="source text", location_mode="precise"):
    return SimpleNamespace(message=message, source="", status=status,
                           quote=quote, location_mode=location_mode)


def test_severity_loader_matches_the_authoritative_matrix_file():
    expected = json.loads((ROOT / "app/plus/blockchain_supervision/data/severity_matrix.json")
                          .read_text(encoding="utf-8"))
    assert rules.load_severity_matrix() == expected
    assert load_severity_matrix() == expected
    assert severity._matrix() == expected


def test_severity_decisions_keep_existing_hit_and_uncertain_gates():
    matrix = severity._matrix()
    high_term = matrix["severity"]["high"][0]
    low_term = matrix["severity"]["low"][0]
    assert severity.map_bs_severity(_finding(high_term, STATUS_HIT)) == "high"
    assert severity.bs_confidence(_finding(high_term, STATUS_HIT)) == "medium"
    assert severity.map_bs_severity(_finding(high_term, STATUS_UNDECIDABLE)) == matrix["severity_gate"]["downgrade_to"]
    assert severity.bs_confidence(_finding(high_term, STATUS_UNDECIDABLE)) == "low"
    assert severity.map_bs_severity(_finding(low_term, STATUS_HIT)) == "low"
    assert severity.map_bs_severity(_finding("neutral", STATUS_HIT)) == matrix["default"]
