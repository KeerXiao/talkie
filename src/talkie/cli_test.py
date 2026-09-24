import pytest

from talkie import providers
from talkie.cli import check_key, main, parse_args
from talkie.client import AuthError


def test_defaults_to_the_hotkey_loop():
    args = parse_args([])
    assert args.record is None
    assert args.verbose is False


def test_record_flag():
    assert parse_args(["--record", "3"]).record == 3.0


def test_rejects_unknown_flags():
    with pytest.raises(SystemExit):
        parse_args(["--nope"])


def test_check_flag():
    assert parse_args(["--check"]).check is True


def test_check_key_reports_a_working_credential(monkeypatch, caplog):
    from talkie.config import Config

    class FakeClient:
        def key_info(self):
            return {"label": "laptop", "usage": 1.5, "limit": 10.0}

    monkeypatch.setattr("talkie.cli._client", lambda config: FakeClient())
    with caplog.at_level("INFO"):
        check_key(Config(api_key="sk-test"))
    assert "key ok" in caplog.text
    assert "laptop" in caplog.text


def test_check_key_surfaces_a_bad_credential(monkeypatch, caplog):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-bad")

    class FakeClient:
        def key_info(self):
            raise AuthError("Missing Authentication header", 401)

    monkeypatch.setattr("talkie.cli._client", lambda config: FakeClient())
    assert main(["--check"]) == 1
    assert "401" in caplog.text


def test_missing_api_key_exits_nonzero(monkeypatch, caplog):
    # Every provider's key, not just OpenRouter's: with any one of them set,
    # startup now succeeds and main() would block in the hotkey loop.
    for provider in providers.PROVIDERS.values():
        monkeypatch.delenv(provider.env_var, raising=False)
    assert main([]) == 1
    assert "OPENROUTER_API_KEY" in caplog.text
