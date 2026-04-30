from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.emerald.coordinator import EaSnapshot

SAMPLE_PAYLOAD = {
    "synced_timestamp": 1642129123099,
    "average_daily_spend": 1.55,
    "daily_consumptions": [
        {
            "date_string": "2024-03-10",
            "total_kwh_of_day": 1.1123,
            "total_cost_of_day": 0.2391,
            "ten_minute_consumptions": [
                {"time_string": "00:00", "kwh": 0.0623, "number_of_flashes": 311},
                {"time_string": "00:10", "kwh": 0.0623, "number_of_flashes": 311},
                {"time_string": "23:50", "kwh": 0.0, "number_of_flashes": 0},
            ],
        }
    ],
}


def test_from_payload_extracts_basic_fields() -> None:
    snap = EaSnapshot.from_payload(SAMPLE_PAYLOAD)
    assert snap.energy_today_kwh == 1.1123
    assert snap.cost_today == 0.2391
    assert snap.average_daily_spend == 1.55
    assert snap.last_synced == datetime.fromtimestamp(1642129123099 / 1000, tz=UTC)
    assert snap.last_synced.tzinfo is UTC


def test_latest_bin_skips_empty_bins() -> None:
    snap = EaSnapshot.from_payload(SAMPLE_PAYLOAD)
    # The 23:50 bin has 0 flashes; latest non-empty is 00:10.
    assert snap.latest_bin_time == "00:10"
    assert snap.latest_bin_kwh == 0.0623


def test_latest_power_w_converts_kwh_to_avg_watts() -> None:
    snap = EaSnapshot.from_payload(SAMPLE_PAYLOAD)
    # 0.0623 kWh over 10 min = 0.0623 * 1000 * 6 W ≈ 373.8 W
    assert snap.latest_power_w == pytest.approx(373.8)


def test_from_payload_handles_missing_data() -> None:
    snap = EaSnapshot.from_payload({})
    assert snap.energy_today_kwh == 0.0
    assert snap.cost_today == 0.0
    assert snap.last_synced is None
    assert snap.latest_bin_kwh is None
    assert snap.latest_power_w is None
