import json
import logging
from types import SimpleNamespace
from typing import Any

from gtd.settings import settings

logger = logging.getLogger(__name__)


_LLM_MAX_ATTEMPTS = 2


def call_llm(system_prompt: str, user_prompt: str) -> dict:
    """Call LLM via LiteLLM and return parsed JSON response.

    Model format: provider/model, e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o.
    Set llm_model=mock to use keyword-based mock responses for testing.
    """
    model = settings.llm_model

    if not model or model == "mock":
        if "艾森豪威尔" in system_prompt:
            return _mock_classify_response(user_prompt)
        return _mock_response(user_prompt)

    return _call_litellm(model, system_prompt, user_prompt)


def _call_litellm(model: str, system_prompt: str, user_prompt: str) -> dict:
    import litellm

    kwargs: dict = {"model": model, "max_tokens": 1024, "temperature": 0.1}

    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    if settings.llm_base_url:
        kwargs["api_base"] = settings.llm_base_url

    last_error: Exception | None = None
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        resp = litellm.completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **kwargs,
        )

        try:
            text = _extract_response_text(resp)
            logger.debug("LLM raw response (%s): %s", model, text[:500])
            return _parse_json(text)
        except ValueError as e:
            last_error = e
            logger.warning(
                "LLM response handling failed (%s) attempt %s/%s: %s",
                model,
                attempt,
                _LLM_MAX_ATTEMPTS,
                e,
            )
            if attempt == _LLM_MAX_ATTEMPTS:
                raise

    raise ValueError(f"LLM response handling failed after {_LLM_MAX_ATTEMPTS} attempts: {last_error}")


def _extract_response_text(resp: Any) -> str:
    """Extract assistant text from LiteLLM responses with defensive fallbacks."""
    choices = _get_field(resp, "choices")
    if not choices:
        raise ValueError(f"LLM returned no choices in response: {_preview_value(resp)}")

    first = choices[0]
    msg = _get_field(first, "message")
    if msg is None:
        raise ValueError(f"LLM choice has no message: {_preview_value(first)}")

    text = _get_field(msg, "content")
    if isinstance(text, list):
        text = _flatten_content_blocks(text)

    if not text:
        fallback = _extract_text_fallback(msg) or _extract_text_fallback(first) or _extract_text_fallback(resp)
        if fallback:
            text = fallback

    if not isinstance(text, str) or not text.strip():
        reasoning = _get_field(msg, "reasoning_content") or _get_field(resp, "reasoning_content")
        raise ValueError(
            "LLM returned empty content"
            + (f" (reasoning_content: {_preview_value(reasoning)})" if reasoning else "")
        )

    return text.strip()


def _extract_text_fallback(obj: Any) -> str | None:
    for key in ("text", "output_text", "completion", "response"):
        value = _get_field(obj, key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    content = _get_field(obj, "content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        flattened = _flatten_content_blocks(content)
        if flattened:
            return flattened

    return None


def _flatten_content_blocks(content: list[Any]) -> str | None:
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            if block.strip():
                parts.append(block.strip())
            continue

        text = _get_field(block, "text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
            continue

        nested = _get_field(block, "content")
        if isinstance(nested, str) and nested.strip():
            parts.append(nested.strip())

    return "\n".join(parts) if parts else None


def _get_field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _preview_value(value: Any, limit: int = 200) -> str:
    try:
        if hasattr(value, "model_dump"):
            rendered = json.dumps(value.model_dump(), ensure_ascii=False)
        elif hasattr(value, "dict"):
            rendered = json.dumps(value.dict(), ensure_ascii=False)
        else:
            rendered = repr(value)
    except Exception:
        rendered = repr(value)

    if len(rendered) > limit:
        return rendered[:limit] + "..."
    return rendered


def _parse_json(text: str) -> dict:
    """Extract JSON object from LLM response.

    Handles: markdown fences, prose surrounding JSON, missing content.
    Raises ValueError with a concise preview when no JSON object is found.
    """
    if text is None:
        raise ValueError("LLM response text is None")

    text = text.strip()

    # Strip markdown fences (```json / ```)
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try a direct parse first (covers clean JSON and arrays)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract the first balanced JSON object from prose
    obj = _extract_json_object(text)
    if obj is not None:
        return obj

    preview = text[:200] if text else "(empty)"
    raise ValueError(f"Cannot extract valid JSON from LLM response: {preview}")


def _extract_json_object(text: str) -> dict | None:
    """Find and parse the first balanced JSON object in *text*."""
    for i, ch in enumerate(text):
        if ch == "{":
            depth = 0
            in_string = False
            escaped = False
            for j in range(i, len(text)):
                current = text[j]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue

                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[i : j + 1])
                        except json.JSONDecodeError:
                            break  # try next "{"
    return None


# ── Mock provider (no API key needed, for testing) ─────

def _mock_classify_response(user_prompt: str) -> dict:
    text = user_prompt.split("\n")[-1].strip()
    q1_kw = ["投诉", "紧急", "今天", "马上", "立即", "截止"]
    q3_kw = ["助理", "帮忙", "让别人", "委派", "打印"]
    q4_kw = ["随便", "无聊", "看看", "测试", "test", "要不要"]
    if any(k in text for k in q1_kw):
        return {"quadrant": "q1", "reasoning": "紧急且重要"}
    if any(k in text for k in q3_kw):
        return {"quadrant": "q3", "reasoning": "紧急但不重要，适合委派"}
    if any(k in text for k in q4_kw):
        return {"quadrant": "q4", "reasoning": "不重要不紧急"}
    return {"quadrant": "q2", "reasoning": "重要但不紧急，适合计划安排"}


def _mock_response(user_prompt: str) -> dict:
    """Keyword-based mock clarify result."""
    # strip clarify prompt prefix: extract the actual user input after last newline
    text = user_prompt.split("\n")[-1].strip()
    if not text:
        text = user_prompt.replace("请澄清以下事项：", "").replace("请澄清以下事項：", "").strip()

    project_kw = ["安排", "组织", "策划", "准备", "装修", "搬家", "招聘"]
    waiting_kw = ["等", "等回复", "等待"]
    trash_kw = ["测试", "test"]
    someday_kw = ["要不要", "也许", "或者", "将来", "未来", "考虑"]

    if any(k in text for k in trash_kw):
        return _result(destination="trash", trash=True, reasoning="无意义输入")

    if any(k in text for k in someday_kw):
        return _result(is_actionable=False, destination="someday_maybe",
                       someday_category="待评估", reasoning="时机未到")

    if any(k in text for k in waiting_kw):
        return _result(
            is_actionable=True,
            next_action={"title": f"跟进：{text}", "context_tag": "@电话",
                         "energy_level": "medium", "estimated_minutes": 10},
            delegate_to="待确认", destination="waiting_for",
            reasoning="需等待他人",
        )

    if any(k in text for k in project_kw):
        return _result(
            is_actionable=True, is_project=True, project_title=text,
            desired_outcome=f"完成：{text}",
            next_action={"title": f"开始处理：{text}的第一步", "context_tag": "@电脑",
                         "energy_level": "medium", "estimated_minutes": 30},
            destination="projects", reasoning="需多个步骤",
        )

    return _result(
        is_actionable=True,
        next_action={"title": text, "context_tag": "@电脑",
                     "energy_level": "low", "estimated_minutes": 10},
        destination="next_actions", reasoning="单一动作",
    )


def _result(**overrides) -> dict:
    r = {
        "is_actionable": False, "needs_clarification": False,
        "clarification_question": None, "is_project": False,
        "project_title": None, "desired_outcome": None,
        "next_action": {"title": None, "context_tag": None, "energy_level": None,
                        "estimated_minutes": None, "is_calendar_required": False},
        "two_minute_rule": False, "delegate_to": None,
        "destination": "trash", "reference_title": None,
        "someday_category": None, "trash": False, "reasoning": "",
    }
    r.update(overrides)
    return r
