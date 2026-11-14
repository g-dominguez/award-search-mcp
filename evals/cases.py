"""
Eval cases: a prompt, what the fake Seats.aero answers with, and checks on Claude's behaviour.

Each check gets a Run (the search_award_seats calls Claude made and its final answer) and
returns True or False. Checks written as a JudgeCheck are graded by a second Claude call
against a written rule, for things plain code can't check reliably.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from evals.fake_seats_aero import Scenario

EVAL_FIXTURES = Path(__file__).parent / "fixtures"
TEST_FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


@dataclass
class Run:
    calls: list[dict]  # arguments of each search_award_seats call, in order
    answer: str

    @property
    def first(self) -> dict:
        return self.calls[0] if self.calls else {}


@dataclass(frozen=True)
class CodeCheck:
    name: str
    test: Callable[[Run], bool]


@dataclass(frozen=True)
class JudgeCheck:
    name: str
    rule: str  # what a good answer does, in plain language


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    scenario: Scenario
    checks: list[CodeCheck | JudgeCheck] = field(default_factory=list)


def day(run_value: str | None) -> date | None:
    return date.fromisoformat(run_value) if run_value else None


def dates_between(run: Run, start_from, start_to, end_from, end_to) -> bool:
    start, end = day(run.first.get("start_date")), day(run.first.get("end_date"))
    return bool(start and end) and start_from <= start <= start_to and end_from <= end <= end_to


searched = CodeCheck("searched", lambda run: len(run.calls) > 0)

DOESNT_PRESENT_FAILURE_AS_NO_SEATS = JudgeCheck(
    "failure_not_presented_as_no_seats",
    "The search tool failed, so nobody knows whether seats exist. A good answer makes clear the "
    "check could not be done. It fails if it tells the user there are no seats or no availability.",
)

CASES = [
    Case(
        name="vague_dates_points_limit",
        prompt="any premium economy seats from Nice to LA in mid-July under 75k?",
        scenario=Scenario(fixture=EVAL_FIXTURES / "nce_lax_premium_mid_july.json"),
        checks=[
            searched,
            CodeCheck("origin_is_nice", lambda run: run.first.get("origins") == ["NCE"]),
            CodeCheck("destination_includes_lax", lambda run: "LAX" in run.first.get("destinations", [])),
            CodeCheck("cabin_premium", lambda run: run.first.get("cabin") == "premium"),
            CodeCheck("points_limit_per_seat", lambda run: run.first.get("points_limit") == 75000),
            CodeCheck(
                "mid_july_dates",
                lambda run: dates_between(run, date(2027, 7, 5), date(2027, 7, 15), date(2027, 7, 15), date(2027, 7, 25)),
            ),
            JudgeCheck(
                "nothing_under_limit",
                "The cheapest seats cost 98,000 points per seat, so nothing is under 75k. A good answer "
                "says there is nothing under 75k. It fails if it claims seats under 75k exist.",
            ),
        ],
    ),
    Case(
        name="missing_destination_asks",
        prompt="anything from Nice in mid-July?",
        scenario=Scenario(fixture=EVAL_FIXTURES / "nce_lax_premium_mid_july.json"),
        checks=[
            CodeCheck("did_not_search", lambda run: not run.calls),
            CodeCheck("asks_a_question", lambda run: "?" in run.answer),
        ],
    ),
    Case(
        name="cities_and_party_size",
        prompt="2 of us want business class from London to New York on March 10 2027, what's available?",
        scenario=Scenario(fixture=EVAL_FIXTURES / "london_nyc_business_mar10.json"),
        checks=[
            searched,
            CodeCheck("london_airports", lambda run: {"LHR", "LGW"} <= set(run.first.get("origins", []))),
            CodeCheck("new_york_airports", lambda run: {"JFK", "EWR"} <= set(run.first.get("destinations", []))),
            CodeCheck("two_seats", lambda run: run.first.get("seats") == 2),
            CodeCheck(
                "exact_date",
                lambda run: run.first.get("start_date") == run.first.get("end_date") == "2027-03-10",
            ),
            CodeCheck("cabin_business", lambda run: run.first.get("cabin") == "business"),
        ],
    ),
    Case(
        name="season_dates",
        prompt="business class from San Francisco to Tokyo sometime in spring, under 90k points each",
        scenario=Scenario(fixture=EVAL_FIXTURES / "sfo_tokyo_business_spring.json"),
        checks=[
            searched,
            CodeCheck("origin_sfo", lambda run: run.first.get("origins") == ["SFO"]),
            CodeCheck("tokyo_airports", lambda run: {"HND", "NRT"} <= set(run.first.get("destinations", []))),
            CodeCheck("points_limit", lambda run: run.first.get("points_limit") in (89999, 90000)),
            CodeCheck(
                "spring_dates",
                lambda run: dates_between(run, date(2027, 3, 1), date(2027, 3, 21), date(2027, 5, 31), date(2027, 6, 21)),
            ),
        ],
    ),
    Case(
        name="no_availability",
        prompt="any first class seats from Nice to Los Angeles on July 17 2027?",
        scenario=Scenario(fixture=TEST_FIXTURES / "empty.json"),
        checks=[
            searched,
            CodeCheck("cabin_first", lambda run: run.first.get("cabin") == "first"),
            JudgeCheck(
                "reports_no_seats",
                "The search ran fine and found no seats. A good answer says there are no seats for that "
                "search. It fails if it says the search failed or invents any flights.",
            ),
        ],
    ),
    Case(
        name="seats_aero_outage",
        prompt="premium economy from London to LA on July 17 2027?",
        scenario=Scenario(status=500, error_body="Internal Server Error"),
        checks=[searched, DOESNT_PRESENT_FAILURE_AS_NO_SEATS],
    ),
    Case(
        name="bad_api_key",
        prompt="premium economy from London to LA on July 17 2027?",
        scenario=Scenario(status=401, error_body="bad_partner_key"),
        checks=[
            searched,
            DOESNT_PRESENT_FAILURE_AS_NO_SEATS,
            CodeCheck("mentions_the_key", lambda run: "key" in run.answer.lower()),
        ],
    ),
    Case(
        name="old_data",
        prompt="premium economy from Heathrow to LAX on July 17 2027 for 2 people?",
        scenario=Scenario(fixture=TEST_FIXTURES / "lhr_lax_match.json", hours_since_seen=120),
        checks=[
            searched,
            CodeCheck("two_seats", lambda run: run.first.get("seats") == 2),
            JudgeCheck(
                "warns_data_is_old",
                "Every result was last seen about 5 days ago. A good answer warns that the seats were last "
                "seen days ago and may be gone, and suggests confirming before transferring points.",
            ),
        ],
    ),
]
