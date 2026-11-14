"""
Generate the eval fixtures: made-up flights in the shape of a Seats.aero search response.

Nothing here is real availability. Flight numbers are fictional (4 digits starting with 9),
and prices, seats and times are invented. Seats.aero's terms don't allow publicly showing
their data for flights more than 60 days out, so the repo only holds synthetic data.

Run from the repo root to rewrite evals/fixtures/:
    .venv/bin/python -m evals.make_fixtures
The random choices are seeded, so the output is the same every time.
"""

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

CABIN_LETTERS = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}
PLACEHOLDER_SEEN = "2026-11-14T00:00:00Z"  # the fake Seats.aero rewrites this on every request


def trip(source, cabin, flights, origin, destination, departs, minutes, points, seats, taxes, currency,
         connections=(), mixed_cabin_pct=None):
    carriers = ", ".join(sorted({f[:2] for f in flights}))
    result = {
        "FlightNumbers": ", ".join(flights),
        "Cabin": cabin,
        "RemainingSeats": seats,
        "MileageCost": points,
        "TotalTaxes": taxes,
        "TaxesCurrency": currency,
        "Carriers": carriers,
        "Stops": len(connections),
        "Connections": list(connections),
        "OriginAirport": origin,
        "DestinationAirport": destination,
        "DepartsAt": departs.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ArrivesAt": (departs + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "TotalDuration": minutes,
        "UpdatedAt": PLACEHOLDER_SEEN,
        "Source": source,
    }
    if mixed_cabin_pct:
        result["MixedCabinPct"] = mixed_cabin_pct
    return result


def items_from(trips: list[dict]) -> list[dict]:
    """Group trips into availability items, one per route, date and loyalty program, like Seats.aero."""
    groups = {}
    for t in trips:
        key = (t["OriginAirport"], t["DestinationAirport"], t["DepartsAt"][:10], t["Source"])
        groups.setdefault(key, []).append(t)

    items = []
    for (origin, destination, day, source), group in sorted(groups.items()):
        letter = CABIN_LETTERS[group[0]["Cabin"]]
        cheapest = min(group, key=lambda t: t["MileageCost"])
        items.append({
            "Route": {"OriginAirport": origin, "DestinationAirport": destination, "Source": source},
            "Date": day,
            f"{letter}Available": True,
            f"{letter}MileageCost": str(cheapest["MileageCost"]),
            f"{letter}RemainingSeats": max(t["RemainingSeats"] for t in group),
            f"{letter}Airlines": ", ".join(sorted({t["Carriers"] for t in group})),
            f"{letter}Direct": any(t["Stops"] == 0 for t in group),
            "TaxesCurrency": cheapest["TaxesCurrency"],
            "Source": source,
            "UpdatedAt": PLACEHOLDER_SEEN,
            "AvailabilityTrips": group,
        })
    return items


def response(trips: list[dict]) -> dict:
    items = items_from(trips)
    return {"data": items, "count": len(items), "hasMore": False, "cursor": 0}


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


def nce_lax_premium_mid_july(rng: random.Random) -> dict:
    """Nice to LA, premium economy, July 8-22 2027. Everything costs 98,000 points or more."""
    trips = []
    for offset in range(15):
        day = date(2027, 7, 8) + timedelta(days=offset)
        if rng.random() < 0.8:
            trips.append(trip("flyingblue", "premium", ["AF9321", "AF9020"], "NCE", "LAX", at(day, 6, 10), 790,
                              98000, rng.randint(2, 9), 35230, "USD", ["CDG"]))
        if rng.random() < 0.6:
            trips.append(trip("flyingblue", "premium", ["KL9472", "KL9601"], "NCE", "LAX", at(day, 6, 25), 815,
                              rng.choice([98000, 112000]), rng.randint(1, 6), 36010, "USD", ["AMS"]))
        if rng.random() < 0.4:
            trips.append(trip("delta", "premium", ["AF9323", "DL9255"], "NCE", "LAX", at(day, 9, 40), 845,
                              rng.choice([125000, 140000]), rng.randint(1, 4), 5600, "USD", ["CDG"],
                              mixed_cabin_pct=rng.choice([None, 12])))
    return response(trips)


def london_nyc_business_mar10(rng: random.Random) -> dict:
    """London to New York, business, March 10 2027. Mixes direct flights, a stop and an unknown seat count."""
    day = date(2027, 3, 10)
    trips = [
        trip("virginatlantic", "business", ["VS9001"], "LHR", "JFK", at(day, 8, 30), 470, 47500, 4, 72060, "GBP"),
        trip("virginatlantic", "business", ["VS9153"], "LHR", "JFK", at(day, 16, 10), 475, 57500, 2, 72060, "GBP"),
        trip("british", "business", ["BA9117"], "LHR", "JFK", at(day, 8, 20), 470, 60000, 1, 85300, "GBP"),
        trip("british", "business", ["BA9183"], "LGW", "JFK", at(day, 11, 15), 480, 68000, 3, 85300, "GBP"),
        trip("american", "business", ["AA9101"], "LHR", "JFK", at(day, 9, 45), 475, 57500, 0, 23440, "USD"),
        trip("united", "business", ["UA9016"], "LHR", "EWR", at(day, 12, 5), 485, 80000, 6, 18920, "USD"),
        trip("aeroplan", "business", ["AC9865", "AC9718"], "LHR", "EWR", at(day, 10, 0), 640, 70000, 2, 21380,
             "CAD", ["YUL"]),
        trip("flyingblue", "business", ["AF9181", "AF9008"], "LGW", "JFK", at(day, 7, 0), 690, 85000, 5, 24300,
             "EUR", ["CDG"], mixed_cabin_pct=14),
    ]
    return response(trips)


def sfo_tokyo_business_spring(rng: random.Random) -> dict:
    """San Francisco to Tokyo, business, March 1 to May 31 2027. Enough results to fill the 20-result cap."""
    trips = []
    day = date(2027, 3, 1)
    while day <= date(2027, 5, 31):
        if rng.random() < 0.7:
            to = rng.choice(["HND", "NRT"])
            trips.append(trip("aeroplan", "business", ["UA9857", "NH9920"], "SFO", to, at(day, 12, 55), 1060,
                              85000, rng.randint(1, 4), 10090, "CAD", ["PVG"], mixed_cabin_pct=15))
        if rng.random() < 0.25:
            trips.append(trip("united", "business", ["UA9837"], "SFO", "NRT", at(day, 11, 20), 680,
                              88000, rng.randint(1, 3), 5600, "USD"))
        if rng.random() < 0.35:
            trips.append(trip("lifemiles", "business", ["NH9107"], "SFO", "HND", at(day, 1, 25), 655,
                              rng.choice([87000, 95000]), rng.randint(1, 2), 4100, "USD"))
        if rng.random() < 0.3:
            trips.append(trip("alaska", "business", ["JL9001"], "SFO", "HND", at(day, 13, 15), 665,
                              120000, rng.randint(1, 6), 6290, "USD"))
        day += timedelta(days=1)
    return response(trips)


FIXTURES = {
    "nce_lax_premium_mid_july.json": nce_lax_premium_mid_july,
    "london_nyc_business_mar10.json": london_nyc_business_mar10,
    "sfo_tokyo_business_spring.json": sfo_tokyo_business_spring,
}


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, build in FIXTURES.items():
        data = build(random.Random(name))  # seeded by file name, so each fixture is stable on its own
        (out_dir / name).write_text(json.dumps(data, indent=1) + "\n")
        trips = sum(len(item["AvailabilityTrips"]) for item in data["data"])
        print(f"{name}: {len(data['data'])} items, {trips} flights")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fixtures")
