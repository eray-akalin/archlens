"""Deterministic security/testing/cicd checks on tiny_service agree with DEFECTS.md (offline).

Scanners don't run here, so their checks must come out `unknown` (never pass on missing data);
`tests/integration/test_scanners_tiny_service.py` covers them with real scanners.
"""

from pathlib import Path

from tests.fixture_repos import MaterializedRepo, answer_key
from tests.unit.rubric.helpers import deterministic_results

SCANNER_CHECKS = {"SEC-01", "SEC-02", "SEC-03", "CI-03"}


def test_answer_key_verdicts(tiny_service: MaterializedRepo, tmp_path: Path) -> None:
    results = deterministic_results(tiny_service.root, tmp_path)
    expected = {row.check: row.expected for row in answer_key() if row.check in results}
    assert set(expected) >= {
        "SEC-07",
        "TEST-01",
        "TEST-03",
        "TEST-04",
        "CI-01",
        "CI-02",
        "CI-04",
        "CI-05",
    }
    for check_id, verdict in expected.items():
        result = results[check_id]
        if check_id in SCANNER_CHECKS:
            assert (result.verdict, result.reason) == ("unknown", "tool_not_run"), check_id
        else:
            assert result.verdict == verdict, (check_id, result.claim)


def test_results_carry_evidence_and_no_secret(
    tiny_service: MaterializedRepo, tmp_path: Path
) -> None:
    results = deterministic_results(tiny_service.root, tmp_path)
    for result in results.values():
        assert result.verdict == "unknown" or result.evidence, result.check_id
    dumped = "".join(r.model_dump_json() for r in results.values())
    assert not any(value in dumped for value in tiny_service.secret_values)
    assert "docker/login-action@v3" in results["CI-05"].claim
