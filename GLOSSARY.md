# Award Search

Lets an AI agent answer questions about award seat availability on request. It only reads availability and never books, alerts or transfers points.

## Language

**Award Seat**:
An airline seat that can be booked with points or miles from a loyalty program instead of cash.
_Avoid_: Reward seat, points seat

**Loyalty Program**:
An airline points or miles scheme that award seats are booked through, such as Aeroplan or Flying Blue. One airline's seats can often be booked through several loyalty programs.
_Avoid_: Source, airline, program (on its own)

**Route Search**:
A question about award seats between given airports, within a date range, in a given cabin and under a points limit.
_Avoid_: Query, lookup

**Points Limit**:
The most points you're willing to spend per seat, not for the whole booking.
_Avoid_: Budget, ceiling, max points total

**Mixed Cabin**:
A flight with a stop where at least one leg is in a lower cabin than the one requested.
_Avoid_: Partial cabin

**Last Seen**:
When Seats.aero last saw an award seat available. Seats can be gone by the time you check.
_Avoid_: Updated at, freshness

**Points Transfer**:
Moving points from a credit card program to an airline's loyalty program before booking. Out of scope for this project.
_Avoid_: Conversion
