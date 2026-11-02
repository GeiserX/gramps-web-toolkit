"""The inherited checker remains unchanged; new rules live in a separate module."""
import inspect
from gwt_check import checks, homonyms


def test_homonym_decisions_outside_run_checks():
    assert "homonym" not in inspect.getsource(checks.run_checks)
    assert "def install(" in inspect.getsource(homonyms)
