import logging

from gtd.db import (
    get_inbox_item,
    set_inbox_quadrant_classified,
    set_inbox_quadrant_classifying,
    set_inbox_quadrant_failed,
)
from gtd.engine.llm import call_llm
from gtd.engine.prompts import CLASSIFY_SYSTEM_PROMPT, CLASSIFY_USER_PROMPT

logger = logging.getLogger(__name__)

_CLASSIFY_MAX_ATTEMPTS = 3
_VALID_QUADRANTS = {"q1", "q2", "q3", "q4"}


def classify(inbox_id: int) -> dict:
    item = get_inbox_item(inbox_id)
    if not item:
        raise ValueError(f"Inbox item {inbox_id} not found")

    set_inbox_quadrant_classifying(inbox_id)
    user_prompt = CLASSIFY_USER_PROMPT.format(raw_text=item["raw_text"])

    last_error: Exception | None = None
    for attempt in range(1, _CLASSIFY_MAX_ATTEMPTS + 1):
        try:
            raw = call_llm(CLASSIFY_SYSTEM_PROMPT, user_prompt)
            result = _validate_classify_result(raw)
            set_inbox_quadrant_classified(
                inbox_id, result["quadrant"], result["reasoning"], source="ai"
            )
            return result
        except Exception as e:
            last_error = e
            logger.warning(
                "Classify failed for inbox %s attempt %s/%s: %s",
                inbox_id, attempt, _CLASSIFY_MAX_ATTEMPTS, e,
            )

    set_inbox_quadrant_failed(inbox_id)
    raise RuntimeError(f"Classify failed after {_CLASSIFY_MAX_ATTEMPTS} attempts: {last_error}")


def _validate_classify_result(result: dict) -> dict:
    quadrant = result.get("quadrant", "q2")
    if quadrant not in _VALID_QUADRANTS:
        logger.warning("Invalid quadrant %s, falling back to q2", quadrant)
        quadrant = "q2"
    reasoning = result.get("reasoning") or ""
    return {"quadrant": quadrant, "reasoning": reasoning}
