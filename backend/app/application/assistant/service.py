"""AssistantService: one conversational turn, with history, redaction and persistence."""

from decimal import Decimal
from uuid import UUID

from app.application.assistant.loop import AssistantRun, run_assistant
from app.application.assistant.tools import ToolContext
from app.application.llm.cost import Pricing
from app.application.llm.redact import redact
from app.application.ports.assistant_store import AssistantStore
from app.application.ports.llm import LLMClient, Usage

MAX_QUESTION_CHARS = 2000
HISTORY_MESSAGES = 6


class InvalidQuestionError(ValueError):
    """Empty or over-long question."""


class ThreadNotFoundError(LookupError):
    """No such thread for this organization."""


class AssistantService:
    def __init__(
        self,
        llm: LLMClient,
        ctx: ToolContext,
        store: AssistantStore,
        pricing: Pricing | None = None,
    ) -> None:
        self._llm = llm
        self._ctx = ctx
        self._store = store
        self._pricing = pricing

    async def start_thread(self, title: str) -> UUID:
        return await self._store.create_thread(redact(title.strip())[:200] or "Conversation")

    async def ask(self, thread_id: UUID, question: str) -> tuple[AssistantRun, UUID]:
        # Secrets or email addresses pasted into a question never reach the model or the database.
        cleaned = redact(question).strip()
        if not cleaned:
            raise InvalidQuestionError("The question is empty.")
        if len(cleaned) > MAX_QUESTION_CHARS:
            raise InvalidQuestionError(
                f"The question is longer than {MAX_QUESTION_CHARS} characters."
            )
        if not await self._store.thread_exists(thread_id):
            raise ThreadNotFoundError(str(thread_id))

        history = [
            (msg.role, msg.content)
            for msg in await self._store.messages(thread_id, HISTORY_MESSAGES)
        ]
        run = await run_assistant(self._llm, self._ctx, cleaned, history=history)
        cost = (
            self._pricing.cost(Usage(run.input_tokens, run.output_tokens))
            if self._pricing
            else Decimal(0)
        )
        return run, await self._store.save_turn(thread_id, run, cost)
