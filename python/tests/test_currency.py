import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_incident_display_amounts_are_explicitly_converted_from_usd():
    incident = json.loads((ROOT / "artifacts" / "incident.json").read_text())
    for event in incident["events"]:
        assert event["amount"] == event["sourceAmountUsd"] * 83.0


def test_metrics_declare_separate_source_and_policy_currencies():
    metrics = json.loads((ROOT / "artifacts" / "sentinel_metrics.json").read_text())
    units = metrics["financial_units"]
    assert units["source_currency"] == "USD"
    assert units["policy_currency"] == "INR"
    assert units["usd_to_inr_rate"] == 83.0
    assert "not an observed" in units["warning"]
