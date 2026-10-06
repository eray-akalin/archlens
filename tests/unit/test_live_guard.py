"""The live guard in tests/conftest.py, exercised in an isolated inner pytest run."""

from pathlib import Path

import pytest

CONFTEST = Path(__file__).parents[1] / "conftest.py"

INNER_INI = """
[pytest]
asyncio_default_fixture_loop_scope = function
markers =
    live: real LLM calls
    unit: fast tests
"""

INNER_TESTS = """
import pytest

@pytest.mark.live
def test_live():
    pass

def test_plain():
    pass
"""


@pytest.fixture
def inner(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makeini(INNER_INI)
    pytester.makepyfile(test_inner=INNER_TESTS)
    return pytester


def test_live_skipped_without_env_var(
    inner: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARCHLENS_LIVE_TESTS", raising=False)
    inner.runpytest("-m", "live").assert_outcomes(skipped=1, deselected=1)


def test_dotenv_file_does_not_enable_live(
    inner: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARCHLENS_LIVE_TESTS", raising=False)
    (inner.path / ".env").write_text("ARCHLENS_LIVE_TESTS=1\n")
    inner.runpytest("-m", "live").assert_outcomes(skipped=1, deselected=1)


def test_live_runs_with_env_var(inner: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHLENS_LIVE_TESTS", "1")
    inner.runpytest("-m", "live").assert_outcomes(passed=1, deselected=1)


def test_unmarked_tests_get_unit_marker(inner: pytest.Pytester) -> None:
    inner.runpytest("-m", "unit").assert_outcomes(passed=1, deselected=1)
