# ADR 0019: Unit-less numbers from the seller's own question are not claims

Status: Accepted (M5)

## Context
A diagnostic (docs/ASSISTANT.md) found that forecast questions ended `unverified` in both assistant engines.
When no forecast exists, the honest answer repeats the seller's own words ("no forecast for the next two weeks"),
and the faithfulness validator (ADR 0015) rejected "two" and "14" because no evidence contains them.

## Decision
`validate_answer` takes the current question. A number is not checked when it is unit-less (no rupee, percent,
points or multiple marker) and the same value appears, unit-less, in the question. A date is not checked when the same
day, month and year appear in the question. Only the current question counts, never earlier messages or answers.

## Consequences
- Figures with a unit that the seller asserts ("my ACOS is 5%") still need evidence before the assistant may say them.
- A unit-less number repeated from the question is accepted even if it is also a claim about the data, for example
  the question "are my 5 campaigns healthy?" lets the answer say "5 campaigns" without evidence. Accepted: the seller
  supplied it, and any figure the answer derives from the data still has to match.
- The check is by value, so a different number never rides on the allowance (tested).
- Scores recorded before this change (docs/ASSISTANT.md) were produced under the stricter rule.
