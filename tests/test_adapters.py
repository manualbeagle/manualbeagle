"""Every adapter's own test cases: offline from recorded fixtures, or live with -m live."""

import pytest

from manualbeagle.adapters import Registry
from manualbeagle.testing import run_case

REG = Registry()
CASES = [(a, c) for a in REG for c in a.spec.tests]
IDS = [f"{a.id}:{c.model}" for a, c in CASES]


@pytest.mark.parametrize(("adapter", "case"), CASES, ids=IDS)
def test_adapter_offline(adapter, case):
    r = run_case(adapter, case, mode="replay")
    if r.status in ("skip", "no-fixture"):
        pytest.skip(r.message)
    if adapter.spec.status == "blocked" and r.status == "blocked":
        pytest.skip(r.message)
    assert r.status == "pass", r.message


@pytest.mark.live
@pytest.mark.parametrize(("adapter", "case"), CASES, ids=IDS)
def test_adapter_live(adapter, case):
    r = run_case(adapter, case, mode="live", check_text=True)
    if r.status == "skip":
        pytest.skip(r.message)
    assert r.status == "pass", r.message
