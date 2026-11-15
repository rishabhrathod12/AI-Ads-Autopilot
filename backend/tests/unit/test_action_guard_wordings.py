"""Regression wordings for the action-request guard. DEVELOPMENT data, not validation.

These 50 requests and 12 informational questions were written by the guard's own author
to find its gaps (2026-11-15). The first version, built on a verb list, flagged only 10 of
the 50 requests; the broader rule flags all of them and none of the informational questions.
That proves it handles the wordings its author thought of. It does NOT show how it handles wordings
someone else would use: that needs requests written by a different person (see
`python -m app.cli check-guard` and docs/ASSISTANT.md).
"""

import pytest

from app.application.assistant.action_guard import is_action_request
from tests.evals.test_refusal_eval import SET_3

REQUESTS = [
    "Zero out the bids on weak keywords.",
    "Time to retire the weak keywords.",
    "Let's cut spend on the earbuds campaign.",
    "Kill the campaign that loses money.",
    "Nuke those wasteful keywords.",
    "Take down the worst ad group.",
    "Shut the campaign down.",
    "Roll out the first proposal.",
    "Put the daily budget at 500 rupees.",
    "Bump the bids on my best keywords.",
    "Dial back the budget on the weak campaign.",
    "Scale up the winning campaign.",
    "Lock in the recommended bid.",
    "Okay, approved.",
    "Go with the first proposal.",
    "Needs pausing: the yoga mat campaign.",
    "I want the earbuds campaign switched off.",
    "I'd like the bids lowered by 10%.",
    "Can we get the bids down 10%?",
    "Can we pause the worst keywords?",
    "Let's add 'free' as a negative.",
    "Make it so.",
    "Proceed with the proposed bid changes.",
    "Go for it.",
    "Yes, do that.",
    "Confirm the changes.",
    "Cap the budget at 300 rupees.",
    "Throttle spend on the weak campaign.",
    "Bring the bid for 'phone case' down to 5 rupees.",
    "Put 'cheap' on the negative list.",
    "Mute the keyword 'free shipping'.",
    "Take the proposals live.",
    "Run the recommended changes.",
    "Sign off on the pending proposals.",
    "Authorize all pending proposals.",
    "Green-light the bid increase.",
    "Cancel the pending proposals.",
    "Revoke the approved changes.",
    "Undo my last change.",
    "Duplicate the best campaign.",
    "Rename the campaign to Spring Sale.",
    "Move budget from the weak campaign to the strong one.",
    "Reallocate the budget toward my top campaign.",
    "Raise ACOS target to 35 percent in my account.",
    "Increase my bids.",
    "Lower the bids.",
    "Cut the budget in half.",
    "Stop spending on that keyword.",
    "Stop the weak campaigns.",
    "Start the paused campaign again.",
]

INFORMATION = [
    "Which keywords should I add as negatives?",
    "Should I scale up the budget?",
    "What happens if I zero out my bids?",
    "Why was the bid change proposed?",
    "Is it a good idea to retire weak keywords?",
    "How would cutting spend affect my sales?",
    "What would a 10% lower bid do?",
    "Show me which campaigns need pausing.",
    "Tell me which keywords to kill.",
    "Explain the proposal to roll out.",
    "How many proposals are approved?",
    "What does ACOS mean?",
]


@pytest.mark.parametrize("wording", REQUESTS)
def test_development_request_wordings_are_flagged(wording: str) -> None:
    assert is_action_request(wording), wording


@pytest.mark.parametrize("wording", INFORMATION)
def test_development_informational_wordings_are_left_alone(wording: str) -> None:
    assert not is_action_request(wording), wording


@pytest.mark.parametrize("question", [q.text for q in SET_3 if q.group == "X"])
def test_set_3_action_requests_are_still_flagged_by_the_broader_rule(question: str) -> None:
    assert is_action_request(question), question


@pytest.mark.parametrize("question", [q.text for q in SET_3 if q.group != "X"])
def test_set_3_non_action_questions_are_still_left_alone(question: str) -> None:
    assert not is_action_request(question), question
