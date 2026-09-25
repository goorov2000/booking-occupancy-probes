"""Новые движки должны работать через тот же диспетчер, что плановый сбор."""
from datetime import date

import pytest

import probes
from probes import bookonline24, frontdesk24, rc_bookings, sutochno


@pytest.mark.parametrize("name,module", [
    ("bookonline24", bookonline24), ("frontdesk24", frontdesk24),
    ("rc-bookings", rc_bookings), ("sutochno", sutochno),
])
def test_engine_available_to_the_scheduled_dispatcher(name, module):
    assert probes.ENGINES[name] is module


def test_broken_aggregator_stays_a_quota_without_network():
    obj, broken = probes.run_recipe(
        "quota", {"engine": "sutochno", "status": "broken",
                  "site": "https://example.com", "source_urls": []},
        date(2026, 9, 8), date(2026, 9, 9),
        fetch=lambda *_: pytest.fail("broken recipe must not contact the host"))
    assert obj["source_kind"] == "aggregator_quota"
    assert obj["status"] == "insufficient_data"
    assert obj["units"] == {}
    assert broken is None
