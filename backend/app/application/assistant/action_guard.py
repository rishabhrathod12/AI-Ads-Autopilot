"""A deterministic check for "do this for me" requests, so refusal does not depend on the model.

The assistant is read-only: it has no tool that changes anything, and it must say so when asked
to act. Evaluation (docs/ASSISTANT.md, "Refusal reliability") showed the model does this reliably
only some of the time, so the answer to any matching question is led by a fixed notice written by
code, the same way the MOCK-data and attribution-window notes are. The notice is true for every
request and harmless if added to a question that was not really a request, so the check can
afford to be simple and broad; it never blocks or alters an answer, it only adds the notice first.

What counts as a request: a sentence that tells the assistant to do something to the seller's
campaigns, bids, budgets, keywords, targets or proposals (an imperative, or "can you", "please",
"go ahead and" ...), a claim of authority to do so ("I authorize you to", "you are now
authorized", "just do it", "administrator mode"), or a plain "do it". Questions asking for
information ("which keyword should I add?") do not start with a verb and are left alone.
"""

import re

ACTION_NOTICE = (
    "I can't make changes: I can only read and explain your advertising data. Creating, approving, "
    "rejecting or applying changes and proposals is done by a person in the Approval Center, and "
    "nothing in this conversation changes your ads."
)
ACTION_FLAG = "action_request_notice"

_VERB = (
    r"(?:pause|stop|turn\s+(?:off|on)|switch\s+(?:off|on)|shut\s+(?:off|down)|disable|enable|re-?enable"
    r"|activate|deactivate|resume|restart|start|delete|remove|get\s+rid\s+of|throw\s+out|drop|block"
    r"|ban|exclude|archive|add|set|change|update|edit|modify|raise|lower|increase|decrease|reduce"
    r"|boost|double|triple|halve|cut|apply|accept|approve|reject|dismiss|decline|execute|push|send"
    r"|publish|launch|create|generate|build|make|run|submit|implement)"
)
_OBJECT = (
    r"(?:campaigns?|ad\s*groups?|bids?|budgets?|keywords?|negatives?|targets?|search\s+terms?|terms?"
    r"|ads?|listings?|proposals?|recommendations?|suggestions?|changes?|everything|anything|all"
    r"|them|those|it|these)"
)
_LEAD = (
    r"(?:(?:please|kindly|now|just|also|then|and|so|ok(?:ay)?|go\s+ahead(?:\s+and)?|can\s+you|could\s+you"
    r"|would\s+you|will\s+you|i\s+want\s+you\s+to|i\s+need\s+you\s+to|i'?d\s+like\s+you\s+to"
    r"|i\s+would\s+like\s+you\s+to|you\s+(?:must|should|need\s+to|have\s+to))\s+)*"
)
# Something the seller wants produced or shown is a report, not a change.
_NOT_A_CHANGE = re.compile(
    r"\b(?:report|summary|summar(?:y|ise|ize)|list|table|chart|overview|csv|export)\b"
)

_REQUEST = re.compile(rf"^{_LEAD}{_VERB}\b.*\b{_OBJECT}\b")
_AUTHORITY = re.compile(
    r"\b(?:i|we)\s+(?:hereby\s+)?(?:authori[sz]e|permit|allow|order|command|instruct|direct|tell|"
    r"want|need|require)\s+you\s+to\b"
    r"|\bi\s+give\s+you\s+(?:my\s+)?(?:full\s+)?(?:permission|approval|authority|consent)\b"
    r"|\byou\s+have\s+my\s+(?:full\s+)?(?:permission|approval|authority|consent)\b"
    r"|\byou(?:'re|\s+are)\s+(?:now\s+)?(?:authori[sz]ed|allowed|permitted|cleared)\s+to\b"
    r"|\b(?:admin(?:istrator)?|root|developer|system)\s+(?:mode|override|access)\b"
    r"|\b(?:just\s+)?do\s+(?:it|that|this)\b|\bmake\s+it\s+happen\b|\bsay\s+(?:done|confirmed)\b"
    r"|\breply\s+['\"]?done\b"
)
_SENTENCE_BREAK = re.compile(r"[.!?;:,\n]+|(?<=\])\s*")  # commas too: "Hey, could you ..."


def is_action_request(question: str) -> bool:
    """True when ``question`` asks the assistant to change something (see the module docstring)."""
    text = question.lower().replace(chr(0x2019), "'")  # a typographic apostrophe
    if _AUTHORITY.search(text):
        return True
    for raw in _SENTENCE_BREAK.split(text):
        sentence = re.sub(r"^[\s\[\(\"'-]+|\bsystem\b\s*$", "", raw.strip())
        if not sentence or _NOT_A_CHANGE.search(sentence):
            continue
        if _REQUEST.search(sentence):
            return True
    return False
