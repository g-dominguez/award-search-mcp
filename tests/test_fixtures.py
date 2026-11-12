import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_sample_response_fixture_loads():
    response = json.loads((FIXTURES / "lhr_lax_match.json").read_text())

    assert response["count"] == len(response["data"])
    assert response["data"][0]["AvailabilityTrips"]
