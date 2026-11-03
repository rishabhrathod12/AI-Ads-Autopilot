"""Configuration safety, the factory, the scripted fake and the llm-check command."""

from decimal import Decimal

import pytest

from app.api.main import create_app
from app.application.llm.budget import BudgetedLLMClient, InMemorySpendLedger
from app.application.llm.cost import Pricing
from app.application.llm.resilient import ResilientLLMClient
from app.application.ports.llm import LLMNotConfiguredError, Message
from app.cli import llm_check
from app.config import Settings
from app.infrastructure.llm.factory import (
    build_budgeted_client,
    build_raw_client,
    limits_of,
    pricing_of,
)
from app.infrastructure.llm.fake import ScriptedLLM, text_reply, tool_reply
from app.startup_checks import UnsafeConfigurationError, validate_llm_config

D = Decimal


def configured(**over: object) -> Settings:
    base: dict[str, object] = {
        "llm_provider": "openai",
        "llm_model": "some-model",
        "openai_api_key": "sk-test-0123456789",
        "llm_input_usd_per_million": D("1"),
        "llm_output_usd_per_million": D("4"),
        "_env_file": None,
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


# ---- safe defaults -------------------------------------------------------------------------
def test_nothing_is_sent_anywhere_by_default() -> None:
    s = Settings(_env_file=None)
    assert s.llm_provider == "none"
    assert s.openai_api_key is None
    validate_llm_config(s)
    with pytest.raises(LLMNotConfiguredError, match="LLM_PROVIDER=none"):
        build_raw_client(s)


def test_default_spend_caps_are_small() -> None:
    s = Settings(_env_file=None)
    assert (s.llm_daily_cap_usd, s.llm_monthly_cap_usd) == (D(1), D(5))


def test_the_api_key_never_appears_in_a_settings_repr() -> None:
    s = configured()
    assert "sk-test-0123456789" not in repr(s)
    assert "sk-test-0123456789" not in str(s.model_dump())


# ---- startup validation --------------------------------------------------------------------
def test_a_complete_configuration_is_accepted() -> None:
    validate_llm_config(configured())
    validate_llm_config(configured(openai_base_url="http://localhost:8080/v1"))
    validate_llm_config(configured(openai_base_url="http://127.0.0.1:8080/v1"))


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"openai_api_key": None}, "OPENAI_API_KEY"),
        ({"llm_model": ""}, "LLM_MODEL"),
        ({"llm_input_usd_per_million": None}, "spend cap needs"),
        ({"llm_output_usd_per_million": None}, "spend cap needs"),
        ({"openai_base_url": "http://api.example.com/v1"}, "https"),
        ({"llm_timeout_seconds": 0}, "TIMEOUT"),
        ({"llm_max_retries": -1}, "MAX_RETRIES"),
        ({"llm_daily_cap_usd": D(0)}, "caps must be positive"),
        ({"llm_monthly_cap_usd": D(-1)}, "caps must be positive"),
    ],
)
def test_an_unsafe_or_incomplete_provider_configuration_is_refused(
    over: dict[str, object], needle: str
) -> None:
    with pytest.raises(UnsafeConfigurationError, match=needle):
        validate_llm_config(configured(**over))


def test_the_app_refuses_to_start_with_an_unmeterable_provider() -> None:
    with pytest.raises(UnsafeConfigurationError, match="spend cap"):
        create_app(configured(llm_input_usd_per_million=None))
    create_app(configured())  # complete configuration starts fine


# ---- factory -------------------------------------------------------------------------------
def test_the_assistant_client_is_budgeted_resilient_and_metered() -> None:
    client = build_budgeted_client(configured(), InMemorySpendLedger(), __import__("uuid").uuid4())
    assert isinstance(client, BudgetedLLMClient)
    assert isinstance(client._inner, ResilientLLMClient)


def test_pricing_and_limits_come_from_settings() -> None:
    s = configured(llm_daily_cap_usd=D("2.5"), llm_monthly_cap_usd=D("20"))
    assert pricing_of(s) == Pricing(D(1), D(4))
    limits = limits_of(s)
    assert (limits.daily_usd, limits.monthly_usd) == (D("2.5"), D(20))
    with pytest.raises(LLMNotConfiguredError, match="prices"):
        pricing_of(configured(llm_input_usd_per_million=None))


def test_a_provider_without_key_or_model_cannot_be_built() -> None:
    with pytest.raises(LLMNotConfiguredError):
        build_raw_client(configured(openai_api_key=None))
    with pytest.raises(LLMNotConfiguredError):
        build_raw_client(configured(llm_model=""))


# ---- scripted fake -------------------------------------------------------------------------
async def test_the_fake_plays_its_script_in_order_and_records_calls() -> None:
    llm = ScriptedLLM([tool_reply(("get_summary", {"days": 7})), text_reply("done")])
    first = await llm.complete([Message("user", "q")], max_output_tokens=10, temperature=None)
    assert first.finish_reason == "tool_calls"
    assert first.message.tool_calls[0].name == "get_summary"
    second = await llm.complete([Message("user", "q2")])
    assert second.message.content == "done"
    assert [c.messages[0].content for c in llm.calls] == ["q", "q2"]
    assert llm.calls[0].max_output_tokens == 10
    assert llm.remaining == 0


async def test_the_fake_can_raise_or_compute_replies() -> None:
    llm = ScriptedLLM(
        [RuntimeError("boom"), lambda msgs, tools: text_reply(f"{len(msgs)} messages")]
    )
    with pytest.raises(RuntimeError, match="boom"):
        await llm.complete([Message("user", "x")])
    reply = await llm.complete([Message("user", "a"), Message("user", "b")])
    assert reply.message.content == "2 messages"


async def test_the_fake_fails_loudly_when_called_beyond_its_script() -> None:
    with pytest.raises(AssertionError, match="more times than"):
        await ScriptedLLM([]).complete([Message("user", "x")])


# ---- llm-check -----------------------------------------------------------------------------
async def test_llm_check_reports_model_tokens_and_cost() -> None:
    lines = await llm_check(ScriptedLLM([text_reply("OK")]), Pricing(D(1), D(2)))
    text = "\n".join(lines)
    assert "model: scripted" in text
    assert "finish_reason: stop" in text
    assert "tokens: 100 in" in text
    assert "estimated cost: $0.0001" in text
    assert "'OK'" in text


async def test_llm_check_never_prints_a_secret_the_model_echoes() -> None:
    lines = await llm_check(
        ScriptedLLM([text_reply("OK sk-proj-abcdefgh12345678")]), Pricing(D(1), D(1))
    )
    assert "sk-proj" not in "\n".join(lines)


def test_assistant_engine_defaults_to_the_single_assistant() -> None:
    from app.config import Settings

    assert Settings(_env_file=None).assistant_engine == "single"
