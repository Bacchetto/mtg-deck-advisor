"""Tests that call real services are skipped unless asked for with --live."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PROJECT_CONFTEST = Path(__file__).parents[1] / "conftest.py"


def test_live_tests_are_skipped_unless_asked_for(pytester: pytest.Pytester) -> None:
    # A throwaway project using this repository's real conftest.
    pytester.makeconftest(PROJECT_CONFTEST.read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers =\n    live: calls real services\n")
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.live
        def test_calls_a_paid_api():
            raise AssertionError("must not run without --live")
        """
    )

    pytester.runpytest().assert_outcomes(skipped=1)
    pytester.runpytest("--live").assert_outcomes(failed=1)
