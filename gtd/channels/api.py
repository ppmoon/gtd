import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from gtd.db import (
    action_count,
    add_to_inbox,
    archive_inbox_item,
    complete_review_step,
    complete_weekly_review,
    create_action,
    create_done_log,
    create_project,
    create_reference,
    create_someday,
    create_waiting_for,
    create_weekly_review,
    done_count,
    get_inbox_item,
    get_integrity_issues,
    get_weekly_review,
    inbox_count,
    list_actions,
    list_done,
    list_inbox,
    list_projects,
    list_reference,
    list_someday,
    list_waiting_for,
    project_count,
    reference_count,
    someday_count,
    update_action,
    update_inbox_quadrant_user,
    waiting_count,
)
from gtd.engine.classify import classify
from gtd.engine.clarify import clarify, confirm
from gtd.models import (
    ActionUpdateRequest,
    ClarifyConfirmRequest,
    InboxAddRequest,
    QuadrantUpdateRequest,
    ReviewStepRequest,
    RouteInboxRequest,
)

VALID_QUADRANTS = {"q1", "q2", "q3", "q4"}

router = APIRouter()
logger = logging.getLogger(__name__)

_tpl_dir = str(Path(__file__).resolve().parent.parent / "templates")
_jinja = Environment(loader=FileSystemLoader(_tpl_dir))


def _render(name: str, **ctx) -> str:
    return _jinja.get_template(name).render(**ctx)


def _nav_counts() -> dict:
    return {
        "inbox": inbox_count(),
        "actions": action_count("next"),
        "projects": project_count("active"),
        "waiting": waiting_count("waiting"),
        "someday": someday_count("active"),
        "reference": reference_count(),
        "done": done_count(),
    }


# ── Capture ────────────────────────────────────────────

@router.post("/api/inbox")
def api_add_inbox(req: InboxAddRequest, background_tasks: BackgroundTasks):
    if not req.text.strip():
        return {"ok": False, "error": "text is empty"}
    rid = add_to_inbox(req.text.strip(), req.source)
    background_tasks.add_task(classify, rid)
    return {"ok": True, "id": rid, "status": "captured"}


@router.get("/api/inbox")
def api_list_inbox(status: str | None = None):
    items = list_inbox(status=status)
    return {"ok": True, "count": len(items), "items": items}


@router.post("/api/inbox/{inbox_id}/classify")
def api_classify_inbox(inbox_id: int):
    item = get_inbox_item(inbox_id)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    try:
        result = classify(inbox_id)
        return {"ok": True, "inbox_id": inbox_id, "result": result}
    except Exception as e:
        logger.exception("Classify failed")
        raise HTTPException(500, f"Classify failed: {e}")


@router.patch("/api/inbox/{inbox_id}/quadrant")
def api_update_quadrant(inbox_id: int, req: QuadrantUpdateRequest):
    item = get_inbox_item(inbox_id)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    if req.quadrant not in VALID_QUADRANTS:
        raise HTTPException(400, f"invalid quadrant: {req.quadrant}")
    update_inbox_quadrant_user(inbox_id, req.quadrant)
    return {"ok": True, "inbox_id": inbox_id, "quadrant": req.quadrant}


# ── Clarify ────────────────────────────────────────────

@router.post("/api/clarify/{inbox_id}")
def api_clarify(inbox_id: int):
    item = get_inbox_item(inbox_id)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    try:
        result = clarify(inbox_id)
        return {"ok": True, "inbox_id": inbox_id, "result": result}
    except Exception as e:
        logger.exception("Clarify failed")
        raise HTTPException(500, f"Clarify failed: {e}")


@router.post("/api/clarify/{inbox_id}/confirm")
def api_confirm(inbox_id: int, req: ClarifyConfirmRequest):
    item = get_inbox_item(inbox_id)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    try:
        result = confirm(inbox_id, req.final_result.model_dump())
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("Confirm failed")
        raise HTTPException(500, f"Confirm failed: {e}")


# ── Route ───────────────────────────────────────────────

ROUTE_HANDLERS = {
    "next_action": lambda item, _req: create_action(title=item["raw_text"], source_inbox_item_id=item["id"]),
    "project": lambda item, _req: create_project(title=item["raw_text"], source_inbox_item_id=item["id"]),
    "waiting": lambda item, _req: create_waiting_for(item=item["raw_text"], person="", source_inbox_item_id=item["id"]),
    "someday": lambda item, _req: create_someday(item=item["raw_text"], source_inbox_item_id=item["id"]),
    "reference": lambda item, _req: create_reference(title=item["raw_text"], source_inbox_item_id=item["id"]),
    "done": lambda item, _req: create_done_log(title=item["raw_text"], source_inbox_item_id=item["id"]),
    "trash": lambda item, _req: None,
}


@router.post("/api/inbox/{inbox_id}/route")
def api_route_inbox(inbox_id: int, req: RouteInboxRequest):
    item = get_inbox_item(inbox_id)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    handler = ROUTE_HANDLERS.get(req.destination)
    if handler is None:
        valid = ", ".join(ROUTE_HANDLERS.keys())
        raise HTTPException(400, f"Invalid destination '{req.destination}'. Valid: {valid}")
    target_id = handler(item, req)
    archive_inbox_item(inbox_id, status="routed")
    return {"ok": True, "destination": req.destination, "target_id": target_id}


# ── Actions ────────────────────────────────────────────

@router.patch("/api/actions/{action_id}")
def api_update_action(action_id: int, req: ActionUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"ok": False, "error": "no fields to update"}
    update_action(action_id, **updates)
    return {"ok": True}


# ── Recommend ──────────────────────────────────────────

@router.get("/api/actions/recommend")
def api_recommend(context_tag: str | None = None,
                  available_minutes: int | None = None,
                  energy_level: str | None = None):
    actions = list_actions(
        status="next",
        context_tag=context_tag,
        energy_level=energy_level,
        max_minutes=available_minutes,
    )

    scored = []
    for a in actions:
        score = 0
        if context_tag and a.get("context_tag") == context_tag:
            score += 50
        if available_minutes and a.get("estimated_minutes"):
            if a["estimated_minutes"] <= available_minutes:
                score += 20
        if energy_level and a.get("energy_level") == energy_level:
            score += 20
        if a.get("project_id"):
            score += 10
        if a.get("due_at"):
            score += 10
        scored.append((score, a))

    scored.sort(key=lambda x: x[0], reverse=True)

    items = []
    for score, a in scored[:5]:
        reasons = []
        if context_tag and a.get("context_tag") == context_tag:
            reasons.append("情境匹配")
        if available_minutes and a.get("estimated_minutes", 999) <= available_minutes:
            reasons.append(f"耗时{a['estimated_minutes']}分钟，在你可用时间内")
        if energy_level and a.get("energy_level") == energy_level:
            reasons.append("精力匹配")
        items.append({
            "id": a["id"],
            "title": a["title"],
            "estimated_minutes": a.get("estimated_minutes"),
            "context_tag": a.get("context_tag"),
            "energy_level": a.get("energy_level"),
            "score": score,
            "recommendation_reason": "；".join(reasons) if reasons else "综合推荐",
        })

    return {"ok": True, "items": items}


# ── Integrity ──────────────────────────────────────────

@router.get("/api/projects/integrity")
def api_integrity():
    issues = get_integrity_issues()
    return {"ok": True, "issues": issues}


# ── Weekly Review ──────────────────────────────────────

@router.post("/api/weekly-review/start")
def api_start_review():
    rid = create_weekly_review()
    review = get_weekly_review(rid)
    return {"ok": True, "review": review}


@router.post("/api/weekly-review/{review_id}/step/{step_key}")
def api_review_step(review_id: int, step_key: str, req: ReviewStepRequest):
    review = get_weekly_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")
    valid_keys = {s["step_key"] for s in review.get("steps", [])}
    if step_key not in valid_keys:
        raise HTTPException(400, f"Invalid step key: {step_key}")
    complete_review_step(review_id, step_key, user_notes=req.user_notes)
    review = get_weekly_review(review_id)
    return {"ok": True, "review": review}


@router.get("/api/weekly-review/{review_id}/summary")
def api_review_summary(review_id: int):
    review = get_weekly_review(review_id)
    if not review:
        raise HTTPException(404, "Review not found")

    issues = get_integrity_issues()
    issues_found = {
        "missing_next_actions": sum(1 for i in issues if i["type"] == "missing_next_action"),
        "stale_waiting_for": sum(1 for i in issues if i["type"] == "stale_waiting_for"),
        "stale_inbox": sum(1 for i in issues if i["type"] == "stale_inbox"),
    }

    complete_weekly_review(
        review_id,
        summary=f"本周回顾完成。发现{issues_found['missing_next_actions']}个项目缺少下一步行动，"
                f"{issues_found['stale_waiting_for']}个等待事项需要跟进。",
        issues_found=issues_found,
    )

    review = get_weekly_review(review_id)
    return {"ok": True, "review": review, "issues": issues}


# ── Web View ───────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
def view_inbox():
    items = list_inbox()
    count = inbox_count()
    html = _render("inbox.html", items=items, count=count, counts=_nav_counts(), active_page="inbox")
    return HTMLResponse(html)


@router.get("/actions", response_class=HTMLResponse)
def view_actions():
    items = list_actions(status="next")
    html = _render("actions.html", items=items, counts=_nav_counts(), active_page="actions")
    return HTMLResponse(html)


@router.get("/projects", response_class=HTMLResponse)
def view_projects():
    items = list_projects(status="active")
    html = _render("projects.html", items=items, counts=_nav_counts(), active_page="projects")
    return HTMLResponse(html)


@router.get("/waiting", response_class=HTMLResponse)
def view_waiting():
    items = list_waiting_for(status="waiting")
    html = _render("waiting.html", items=items, counts=_nav_counts(), active_page="waiting")
    return HTMLResponse(html)


@router.get("/someday", response_class=HTMLResponse)
def view_someday():
    items = list_someday(status="active")
    html = _render("someday.html", items=items, counts=_nav_counts(), active_page="someday")
    return HTMLResponse(html)


@router.get("/reference", response_class=HTMLResponse)
def view_reference():
    items = list_reference()
    html = _render("reference.html", items=items, counts=_nav_counts(), active_page="reference")
    return HTMLResponse(html)


@router.get("/done", response_class=HTMLResponse)
def view_done():
    items = list_done()
    html = _render("done.html", items=items, counts=_nav_counts(), active_page="done")
    return HTMLResponse(html)
