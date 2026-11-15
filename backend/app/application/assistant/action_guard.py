"""A deterministic check for "do this for me" requests, so refusal does not depend on the model.

The assistant is read-only: it has no tool that changes anything, and it must say so when asked
to act. Evaluation (docs/ASSISTANT.md, "Refusal reliability") showed the model does this reliably
only some of the time, so the answer to any matching question is led by a fixed notice written by
code, the same way the MOCK-data and attribution-window notes are. The notice is true for every
request and harmless if added to a question that was not really a request, so the check can
afford to be simple and broad; it never blocks or alters an answer, it only adds the notice first.

What counts as a request, in two layers. (1) The original verb list: a sentence that starts with a
known action verb ("pause", "approve", ...) and names an account object. (2) Added after a
60-wording check showed the verb list caught only about 1 in 5 of varied phrasings: any
imperative-looking sentence about an account object ("Kill the campaign ...", "Zero out the
bids ...", "Time to retire ...", "Needs pausing", "Let's cut spend", "Can we get the bids down?",
"I want the campaign switched off") is a request UNLESS it is clearly a request for information
(it opens with what / which / why / how / is / should ..., or with show / tell / explain / list /
compare / estimate ...). A claim of authority ("I authorize you to", "administrator mode",
"just do it", "go for it", "proceed") is a request too. Wrongly flagging an informational
sentence only adds a harmless true note; missing a request is the failure that matters.
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
    r"\b(?:report|summary|summar(?:y|ise|ize)|table|chart|overview|csv|export)\b"
    r"|(?<!negative )(?<!negatives )\blist\b"  # a report to show, but not "the negative list"
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
_SENTENCE_BREAK = re.compile(r"[.!?;,\n]+|(?<=\])\s*")  # commas too: "Hey, could you ..."

# Layer 2 -------------------------------------------------------------------------------------
_OBJECT_2 = (
    r"(?:campaigns?|ad\s*groups?|bids?|budgets?|keywords?|negatives?|negative\s+list|targets?"
    r"|search\s+terms?|terms?|ads?|listings?|proposals?|recommendations?|suggestions?|changes?"
    r"|spend(?:ing)?|acos|roas|everything|anything|all|them|those|it|these)"
)
# A sentence that opens with one of these asks for information or is not an instruction.
_INFO_OPENER = re.compile(
    r"^(?:what|which|who|whom|whose|why|how|when|where|whether|is|are|was|were|am|do|does|did|has"
    r"|have|had|should|might|may|any|there|show|tell|explain|list|give|summari[sz]e|compare|find"
    r"|check|see|describe|display|report|analy[sz]e|calculate|identify|rank|sort|remind|walk|help"
    r"|estimate|predict|forecast|project|review|assess|evaluate|look|pull|recommend|suggest|advise"
    r"|name|break|highlight|outline|define|clarify|justify|compute|measure|count|rate"
    r"|guarantee|promise|assure|swear|ensure|pledge)\b"  # asks for an assurance, not a change
)
# Opening words of statements, not instructions ("My campaign stopped spending").
_STATEMENT_OPENER = re.compile(
    r"^(?:my|our|your|the|this|that|these|those|i|we|you|it|its|they|he|she|a|an|last|yesterday"
    r"|today|tomorrow|here|in|on|at|for|from|with|after|before|during|because|if|but|since|while"
    r"|thanks|thank|hello|hi|hey|ok|okay|yes|no|sure|great|good)\b"
)
_ASK_TO_DO = re.compile(  # "Can we get the bids down?", "Could you ..." (not "can you explain")
    r"^(?:can|could|would|will|shall)\s+(?:you|we|i)\s+(?!(?:please\s+)?(?:explain|show|tell|list|give"
    r"|summari[sz]e|compare|find|check|see|describe|display|report|analy[sz]e|calculate|identify"
    r"|rank|sort|remind|walk|help|estimate|predict|forecast|share|pull|look)\b)"
)
_WANT = re.compile(
    r"\b(?:i|we)(?:'d|\s+would|\s+will|\s+do)?\s*(?:want|need|like|wish|expect|require)\s+(?:you\s+to\s+)?"
)
_LETS = re.compile(r"^let(?:'s|s|\s+us)\b")
_NEEDS_DOING = re.compile(  # "Needs pausing: ...", "...need to be paused"
    r"\b(?:needs?|has\s+to|have\s+to|must|ought\s+to)\b.{0,25}\b(?:paus|stopp|disabl|delet|remov|cut|lower"
    r"|rais|chang|updat|turn|switch|block|retir|kill|shut|approv|reject|apply|appli)"
)
_APPROVAL = re.compile(
    r"^(?:approved?|confirm(?:ed)?|proceed|agreed|accepted|go\s+for\s+it|go\s+ahead|green-?light"
    r"|sign\s+off|make\s+it\s+so|do\s+it|yes\s+do)\b"
)
_IMPERATIVE_EXTRA = re.compile(
    r"^(?:needs?|time\s+to|go\s+with|move|put|bring|take|get|keep|leave|let)\b"
)


def _opening(sentence: str) -> str:
    """The sentence without courtesy openers such as "please" or "ok so"."""
    return re.sub(
        r"^(?:(?:please|kindly|now|just|also|then|and|so|ok(?:ay)?|well|right)\s+)+", "", sentence
    )


def _is_request_sentence(sentence: str) -> bool:
    s = _opening(sentence)
    if not s or _NOT_A_CHANGE.search(s) or _INFO_OPENER.match(s):
        return False
    has_object = bool(re.search(rf"\b{_OBJECT_2}\b", s))
    if _APPROVAL.match(s):
        return True
    if _ASK_TO_DO.match(s):
        return has_object
    if _WANT.search(s):
        return has_object
    if _LETS.match(s) or _NEEDS_DOING.search(s) or _IMPERATIVE_EXTRA.match(s):
        return has_object
    if _STATEMENT_OPENER.match(s):
        return False
    return has_object  # an imperative-looking sentence about an account object


def is_action_request(question: str) -> bool:
    """True when ``question`` asks the assistant to change something (see the module docstring)."""
    text = question.lower().replace(chr(0x2019), "'")  # a typographic apostrophe
    if _AUTHORITY.search(text):
        return True
    for raw in _SENTENCE_BREAK.split(text):
        sentence = re.sub(r"^[\s\[\(\"'-]+|\bsystem\b\s*$", "", raw.strip())
        if not sentence or _NOT_A_CHANGE.search(sentence):
            continue
        if _REQUEST.search(sentence) or _is_request_sentence(sentence):
            return True
    return False
