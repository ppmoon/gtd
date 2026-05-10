import json
import logging

from gtd.db import (
    add_to_inbox,
    create_action,
    create_done_log,
    create_project,
    create_reference,
    create_someday,
    create_waiting_for,
    get_inbox_item,
    set_inbox_clarified,
    set_inbox_clarifying,
)
from gtd.engine.llm import call_llm
from gtd.engine.prompts import CLARIFY_SYSTEM_PROMPT, CLARIFY_USER_PROMPT

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    "is_actionable", "needs_clarification", "is_project",
    "project_title", "desired_outcome", "next_action",
    "two_minute_rule", "delegate_to", "destination",
    "reference_title", "someday_category", "trash", "reasoning",
]

VALID_DESTINATIONS = {
    "projects", "next_actions", "waiting_for", "someday_maybe",
    "reference", "trash", "done_log",
}


def clarify(inbox_id: int) -> dict:
    """Run clarify engine on an inbox item. Returns the LLM result dict."""
    item = get_inbox_item(inbox_id)
    if not item:
        raise ValueError(f"Inbox item {inbox_id} not found")

    set_inbox_clarifying(inbox_id)

    user_prompt = CLARIFY_USER_PROMPT.format(raw_text=item["raw_text"])
    result = call_llm(CLARIFY_SYSTEM_PROMPT, user_prompt)

    result = _validate(result)
    set_inbox_clarified(inbox_id, result)
    return result


def confirm(inbox_id: int, final_result: dict) -> dict:
    """Confirm and persist the clarify result to the appropriate list."""
    item = get_inbox_item(inbox_id)
    if not item:
        raise ValueError(f"Inbox item {inbox_id} not found")

    dest = final_result.get("destination", "trash")
    created: dict[str, int] = {}

    if dest == "projects":
        pid = create_project(
            title=final_result.get("project_title") or item["raw_text"],
            desired_outcome=final_result.get("desired_outcome"),
            source_inbox_item_id=inbox_id,
        )
        created["project_id"] = pid

        na = final_result.get("next_action") or {}
        if na and na.get("title"):
            aid = create_action(
                title=na["title"],
                project_id=pid,
                source_inbox_item_id=inbox_id,
                context_tag=na.get("context_tag"),
                energy_level=na.get("energy_level"),
                estimated_minutes=na.get("estimated_minutes"),
                is_calendar_required=na.get("is_calendar_required", False),
            )
            created["action_id"] = aid

    elif dest == "next_actions":
        na = final_result.get("next_action") or {}
        title = na.get("title") or item["raw_text"]
        aid = create_action(
            title=title,
            source_inbox_item_id=inbox_id,
            context_tag=na.get("context_tag"),
            energy_level=na.get("energy_level"),
            estimated_minutes=na.get("estimated_minutes"),
            is_calendar_required=na.get("is_calendar_required", False),
        )
        created["action_id"] = aid

    elif dest == "waiting_for":
        wid = create_waiting_for(
            item=final_result.get("next_action", {}).get("title") or item["raw_text"],
            person=final_result.get("delegate_to") or "",
            source_inbox_item_id=inbox_id,
        )
        created["waiting_for_id"] = wid

    elif dest == "someday_maybe":
        sid = create_someday(
            item=item["raw_text"],
            category=final_result.get("someday_category"),
            source_inbox_item_id=inbox_id,
        )
        created["someday_id"] = sid

    elif dest == "reference":
        rid = create_reference(
            title=final_result.get("reference_title") or item["raw_text"],
            content=item["raw_text"],
            source_inbox_item_id=inbox_id,
        )
        created["reference_id"] = rid

    elif dest == "done_log":
        did = create_done_log(
            title=item["raw_text"],
            completion_type="two_minute_rule",
            source_inbox_item_id=inbox_id,
        )
        created["done_log_id"] = did

    elif dest == "trash":
        pass  # just mark clarified, no entity created

    # Update inbox item with final result
    set_inbox_clarified(inbox_id, final_result)

    return {"inbox_id": inbox_id, "status": "clarified", "created_entities": created}


def _validate(result: dict) -> dict:
    """Validate and normalize clarify result."""
    errors = []

    for field in REQUIRED_FIELDS:
        if field not in result:
            errors.append(f"missing field: {field}")
            result[field] = None

    if result.get("destination") not in VALID_DESTINATIONS:
        errors.append(f"invalid destination: {result.get('destination')}")
        result["destination"] = "trash"

    if errors:
        logger.warning("Clarify validation issues: %s", errors)

    return result
