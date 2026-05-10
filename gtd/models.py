from pydantic import BaseModel


# ── Capture ────────────────────────────────────────────

class InboxAddRequest(BaseModel):
    text: str
    source: str = "manual"


# ── Clarify ────────────────────────────────────────────

class NextActionSuggestion(BaseModel):
    title: str | None = None
    context_tag: str | None = None
    energy_level: str | None = None
    estimated_minutes: int | None = None
    is_calendar_required: bool = False


class ClarifyResult(BaseModel):
    is_actionable: bool = False
    needs_clarification: bool = False
    clarification_question: str | None = None
    is_project: bool = False
    project_title: str | None = None
    desired_outcome: str | None = None
    next_action: NextActionSuggestion | None = None
    two_minute_rule: bool = False
    delegate_to: str | None = None
    destination: str = "trash"
    reference_title: str | None = None
    someday_category: str | None = None
    trash: bool = False
    reasoning: str = ""


class ClarifyConfirmRequest(BaseModel):
    final_result: ClarifyResult


# ── Route ────────────────────────────────────────────────

class RouteInboxRequest(BaseModel):
    destination: str  # next_action | project | waiting | someday | reference | done | trash


# ── Actions ────────────────────────────────────────────

class ActionUpdateRequest(BaseModel):
    title: str | None = None
    context_tag: str | None = None
    energy_level: str | None = None
    estimated_minutes: int | None = None
    status: str | None = None


# ── Weekly Review ──────────────────────────────────────

class ReviewStepRequest(BaseModel):
    user_notes: str | None = None
