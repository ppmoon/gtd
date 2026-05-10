import pytest
from types import SimpleNamespace
from unittest.mock import patch

from gtd.engine import llm as llm_module
from gtd.engine.llm import (
    _extract_json_object,
    _extract_response_text,
    _mock_response,
    _parse_json,
)


# ── _parse_json ─────

def test_parse_clean_json():
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_fenced_json_no_lang():
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_fenced_json_with_lang():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_prose_before_json():
    assert _parse_json('Here is the result:\n{"a": 1}') == {"a": 1}


def test_parse_prose_after_json():
    assert _parse_json('{"a": 1}\nHope this helps.') == {"a": 1}


def test_parse_prose_surrounding_json():
    assert _parse_json('Start text\n{"a": 1}\nEnd text') == {"a": 1}


def test_parse_nested_json():
    assert _parse_json('{"a": {"b": [1, 2, 3]}}') == {"a": {"b": [1, 2, 3]}}


def test_parse_none_raises_value_error():
    with pytest.raises(ValueError, match="None"):
        _parse_json(None)


def test_parse_garbage_raises_value_error():
    with pytest.raises(ValueError, match="Cannot extract"):
        _parse_json("not json at all")


def test_parse_empty_string_raises_value_error():
    with pytest.raises(ValueError):
        _parse_json("")


def test_parse_incomplete_json_raises():
    with pytest.raises(ValueError, match="Cannot extract"):
        _parse_json('{"a": ')


def test_parse_json_with_braces_inside_string():
    assert _parse_json('prefix {"a": "hello {world}", "b": 1} suffix') == {
        "a": "hello {world}",
        "b": 1,
    }


# ── _extract_json_object ─────

def test_extract_simple_object():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_from_prose():
    assert _extract_json_object('prefix {"a": 1} suffix') == {"a": 1}


def test_extract_first_object_only():
    result = _extract_json_object('{"a": 1} garbage {"b": 2}')
    assert result == {"a": 1}


def test_extract_handles_invalid_brace_pairs():
    # invalid JSON between first { and matching } — should try next {
    assert _extract_json_object('{"a": invalid} {"b": 2}') == {"b": 2}


def test_extract_no_object_returns_none():
    assert _extract_json_object("no braces here") is None


# ── _extract_response_text ─────

def test_extract_response_text_from_string_content():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
    )
    assert _extract_response_text(resp) == '{"ok": true}'


def test_extract_response_text_from_content_blocks():
    resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=[{"text": '{"ok": true}'}])
            )
        ]
    )
    assert _extract_response_text(resp) == '{"ok": true}'


def test_extract_response_text_raises_on_missing_choices():
    with pytest.raises(ValueError, match="no choices"):
        _extract_response_text(SimpleNamespace(choices=None))


def test_extract_response_text_raises_on_empty_content():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, reasoning_content="thoughts"))]
    )
    with pytest.raises(ValueError, match="empty content"):
        _extract_response_text(resp)


def test_call_litellm_retries_after_bad_first_response():
    bad = SimpleNamespace(choices=None)
    good = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
    )

    with patch.object(llm_module.settings, "llm_api_key", ""), patch.object(
        llm_module.settings, "llm_base_url", ""
    ), patch("litellm.completion", side_effect=[bad, good]) as mocked_completion:
        result = llm_module._call_litellm("anthropic/test-model", "sys", "user")

    assert result == {"ok": True}
    assert mocked_completion.call_count == 2


# ── _mock_response (existing behavior must be preserved) ─────

def test_mock_trash():
    r = _mock_response("请澄清以下事项：\ntest message")
    assert r["destination"] == "trash"
    assert r["trash"] is True


def test_mock_someday():
    r = _mock_response("请澄清以下事项：\n要不要去旅游")
    assert r["destination"] == "someday_maybe"
    assert r["is_actionable"] is False


def test_mock_waiting():
    r = _mock_response("请澄清以下事项：\n等回复")
    assert r["destination"] == "waiting_for"


def test_mock_project():
    r = _mock_response("请澄清以下事项：\n安排会议")
    assert r["destination"] == "projects"
    assert r["is_project"] is True


def test_mock_default_action():
    r = _mock_response("请澄清以下事项：\n买牛奶")
    assert r["destination"] == "next_actions"
    assert r["is_actionable"] is True
