from app.logging import REDACTED, redact_secrets


def test_sensitive_keys_are_redacted() -> None:
    out = redact_secrets(
        None,
        "info",
        {
            "event": "x",
            "refresh_token": "abc",
            "OPENAI_API_KEY": "k",
            "Authorization": "Bearer z",
            "ok": 1,
        },
    )
    assert out["refresh_token"] == REDACTED
    assert out["OPENAI_API_KEY"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["ok"] == 1
    assert out["event"] == "x"
