"""Unit tests for secret_hygiene.sanitizer."""

from __future__ import annotations

from typing import Any

from .sanitizer import REDACTED_SENTINEL, redact_secrets

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Invoke redact_secrets with the structlog 3-arg shape."""
    result = redact_secrets(None, "info", event_dict)
    assert isinstance(result, dict)
    return result


# ---------------------------------------------------------------------------
# Value-pattern redaction
# ---------------------------------------------------------------------------


class TestValuePatternRedaction:
    def test_anthropic_key_in_message_is_redacted(self) -> None:
        ed = {"msg": "leaked: sk-ant-abcdef1234567890XYZAA"}
        out = _call(ed)
        assert out["msg"] == REDACTED_SENTINEL

    def test_clean_string_passes_through(self) -> None:
        ed = {"msg": "hello world"}
        out = _call(ed)
        assert out["msg"] == "hello world"

    def test_github_classic_token_in_value(self) -> None:
        ed = {"detail": "token=ghp_abcdefghij1234567890abcdefghij"}
        out = _call(ed)
        assert out["detail"] == REDACTED_SENTINEL


# ---------------------------------------------------------------------------
# Key-name redaction
# ---------------------------------------------------------------------------


class TestKeyNameRedaction:
    def test_api_key_value_redacted_regardless(self) -> None:
        ed = {"api_key": "literally anything"}
        out = _call(ed)
        assert out["api_key"] == REDACTED_SENTINEL

    def test_password_int_value_redacted(self) -> None:
        ed = {"password": 12345}
        out = _call(ed)
        assert out["password"] == REDACTED_SENTINEL

    def test_token_key_redacted(self) -> None:
        ed = {"token": "some-value"}
        out = _call(ed)
        assert out["token"] == REDACTED_SENTINEL

    def test_secret_key_redacted(self) -> None:
        ed = {"secret": "my-secret"}
        out = _call(ed)
        assert out["secret"] == REDACTED_SENTINEL

    def test_authorization_key_redacted(self) -> None:
        ed = {"authorization": "Bearer xyz"}
        out = _call(ed)
        assert out["authorization"] == REDACTED_SENTINEL

    def test_bearer_key_redacted(self) -> None:
        ed = {"bearer": "xyz"}
        out = _call(ed)
        assert out["bearer"] == REDACTED_SENTINEL

    def test_case_insensitive_key_matching(self) -> None:
        # casefold() should catch API_KEY, Api_Key, etc.
        ed = {"API_KEY": "some_value", "Password": "abc"}
        out = _call(ed)
        assert out["API_KEY"] == REDACTED_SENTINEL
        assert out["Password"] == REDACTED_SENTINEL

    def test_key_name_redaction_replaces_nested_dict(self) -> None:
        """Sensitive key with dict value is replaced wholesale by sentinel."""
        result = _call({"api_key": {"nested": "anything"}})
        assert result["api_key"] == REDACTED_SENTINEL

    def test_suffix_key_user_api_key_redacted(self) -> None:
        """Keys ending in _key/_token/_secret/_password are caught by suffix match."""
        ed = {"user_api_key": "anything", "github_token": "xyz", "db_password": "pw"}
        out = _call(ed)
        assert out["user_api_key"] == REDACTED_SENTINEL
        assert out["github_token"] == REDACTED_SENTINEL
        assert out["db_password"] == REDACTED_SENTINEL


# ---------------------------------------------------------------------------
# Nested dict recursion
# ---------------------------------------------------------------------------


class TestNestedDict:
    def test_nested_token_key_redacted(self) -> None:
        ed: dict[str, Any] = {"outer": {"token": "abc"}}
        out = _call(ed)
        assert out["outer"]["token"] == REDACTED_SENTINEL

    def test_nested_secret_value_redacted(self) -> None:
        ed: dict[str, Any] = {
            "meta": {"key": "sk-ant-abcdef1234567890XYZA"},
        }
        out = _call(ed)
        assert out["meta"]["key"] == REDACTED_SENTINEL

    def test_clean_nested_passes_through(self) -> None:
        ed: dict[str, Any] = {"outer": {"safe": "value"}}
        out = _call(ed)
        assert out["outer"]["safe"] == "value"


# ---------------------------------------------------------------------------
# List / tuple type preservation
# ---------------------------------------------------------------------------


class TestListTuplePreservation:
    def test_list_with_secret_redacted_and_stays_list(self) -> None:
        ed: dict[str, Any] = {"things": ["normal", "sk-ant-abcdef1234567890XYZA"]}
        out = _call(ed)
        assert isinstance(out["things"], list)
        assert out["things"][0] == "normal"
        assert out["things"][1] == REDACTED_SENTINEL

    def test_tuple_with_secret_redacted_and_stays_tuple(self) -> None:
        ed: dict[str, Any] = {"t": ("a", "sk-ant-abcdef1234567890XYZA")}
        out = _call(ed)
        assert isinstance(out["t"], tuple)
        assert out["t"][0] == "a"
        assert out["t"][1] == REDACTED_SENTINEL

    def test_list_all_clean_passes_through(self) -> None:
        ed: dict[str, Any] = {"items": ["foo", "bar"]}
        out = _call(ed)
        assert out["items"] == ["foo", "bar"]


# ---------------------------------------------------------------------------
# Pass-through for non-string, non-sensitive values
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_int_float_bool_none_pass_through(self) -> None:
        ed: dict[str, Any] = {
            "count": 42,
            "ok": True,
            "nil": None,
            "ratio": 3.14,
        }
        out = _call(ed)
        assert out["count"] == 42
        assert out["ok"] is True
        assert out["nil"] is None
        assert out["ratio"] == 3.14


# ---------------------------------------------------------------------------
# structlog processor shape contract
# ---------------------------------------------------------------------------


class TestStructlogShape:
    def test_returns_dict_with_ignored_first_two_args(self) -> None:
        result = redact_secrets(None, "info", {"k": "v"})
        assert result == {"k": "v"}

    def test_first_arg_ignored(self) -> None:
        result = redact_secrets("logger-object", "warning", {"x": 1})
        assert result == {"x": 1}

    def test_returns_same_object(self) -> None:
        ed: dict[str, Any] = {"k": "v"}
        result = redact_secrets(None, "debug", ed)
        assert result is ed


# ---------------------------------------------------------------------------
# Fix D: cycle guard — circular reference must not cause RecursionError
# ---------------------------------------------------------------------------


class TestCycleGuard:
    def test_circular_dict_does_not_crash(self) -> None:
        d: dict[str, Any] = {"safe": "value"}
        d["ref"] = d  # circular reference
        # Must not raise RecursionError; result content is not specified beyond no-crash.
        result = redact_secrets(None, "info", d)
        assert result is d

    def test_deeply_nested_does_not_crash(self) -> None:
        # Build a 25-level deep nest (exceeds _MAX_DEPTH=20).
        inner: Any = {"leaf": "value"}
        for _ in range(25):
            inner = {"child": inner}
        ed: dict[str, Any] = {"deep": inner}
        # Should not raise RecursionError.
        redact_secrets(None, "info", ed)


# ---------------------------------------------------------------------------
# Fix E: non-string dict keys must not crash
# ---------------------------------------------------------------------------


class TestNonStringKeys:
    def test_integer_key_does_not_crash(self) -> None:
        ed: dict[Any, Any] = {42: "value", "normal": "clean"}
        # Passing a non-dict MutableMapping-shaped value as event_dict is unusual,
        # but non-string keys inside a nested dict value must be handled.
        outer: dict[str, Any] = {"data": ed}
        result = _call(outer)
        # The nested dict with int key should survive without AttributeError.
        assert isinstance(result["data"], dict)
        assert result["data"][42] == "value"

    def test_integer_key_with_secret_value_survives(self) -> None:
        outer: dict[str, Any] = {"data": {42: "sk-ant-abcdef1234567890XYZA"}}
        result = _call(outer)
        # Value under int key should be redacted by value-pattern scan.
        assert result["data"][42] == REDACTED_SENTINEL


# ---------------------------------------------------------------------------
# Fix F: bytes, set, frozenset handling
# ---------------------------------------------------------------------------


class TestBytesSetFrozenset:
    def test_bytes_with_secret_redacted(self) -> None:
        ed: dict[str, Any] = {"data": b"sk-ant-abcdefghij1234567890XYZextended"}
        out = _call(ed)
        assert out["data"] == REDACTED_SENTINEL

    def test_bytes_without_secret_passes_through(self) -> None:
        ed: dict[str, Any] = {"data": b"hello world"}
        out = _call(ed)
        assert out["data"] == b"hello world"

    def test_set_with_secret_element_redacted(self) -> None:
        ed: dict[str, Any] = {"tags": {"normal", "sk-ant-abcdefghij1234567890XYZextended"}}
        out = _call(ed)
        assert isinstance(out["tags"], set)
        assert REDACTED_SENTINEL in out["tags"]
        assert "normal" in out["tags"]

    def test_frozenset_with_secret_element_redacted(self) -> None:
        ed: dict[str, Any] = {
            "tags": frozenset({"normal", "sk-ant-abcdefghij1234567890XYZextended"})
        }
        out = _call(ed)
        assert isinstance(out["tags"], frozenset)
        assert REDACTED_SENTINEL in out["tags"]
        assert "normal" in out["tags"]

    def test_frozenset_clean_passes_through(self) -> None:
        ed: dict[str, Any] = {"tags": frozenset({"a", "b"})}
        out = _call(ed)
        assert out["tags"] == frozenset({"a", "b"})
