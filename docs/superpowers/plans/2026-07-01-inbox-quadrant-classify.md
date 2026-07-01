# Inbox 四象限 AI 自动分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically classify every inbox capture into Eisenhower quadrants (Q1–Q4) via background AI, surface results in list + matrix UI, and inject quadrant-specific strategies into the existing clarify flow.

**Architecture:** Independent `classify.py` engine parallel to `clarify.py`; new quadrant columns on `inbox_items`; capture paths enqueue `classify(inbox_id)` via FastAPI `BackgroundTasks` (web) or direct/thread calls (CLI/Feishu); clarify reads `quadrant` and appends strategy snippets to the system prompt.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (WAL), LiteLLM (`gtd/engine/llm.py`), Jinja2 + vanilla JS, pytest

## Global Constraints

- Quadrant values: `q1` | `q2` | `q3` | `q4` (NULL when unclassified)
- Quadrant status lifecycle: `pending` → `classifying` → `classified` | `failed`
- Classify retry: max **3** attempts at `classify.py` layer (independent of `call_llm` JSON parse retries)
- On 3 failures: `quadrant_status = failed`, `quadrant = NULL`
- User override via PATCH sets `quadrant_source = user`, `quadrant_status = classified`, does **not** re-trigger AI
- Mock mode (`llm_model = mock`): fully offline keyword heuristics
- Web capture: async via `BackgroundTasks` (do not block POST response)
- Feishu: classify in daemon thread after reply
- CLI `add`: synchronous `classify()` so terminal shows result immediately
- List sort default: Q1 → Q2 → Q3 → Q4 → unclassified; same quadrant by `created_at` DESC
- Poll interval: 5s when any item has `quadrant_status = classifying`, else 30s
- No E2E browser tests; pytest only
- Tests run: `.venv/bin/pytest`
- Dev server: `.venv/bin/uvicorn gtd.main:app --reload --host 127.0.0.1 --port 8420`

## File Map

| File | Responsibility |
|------|----------------|
| `gtd/db.py` | Quadrant columns migration + CRUD |
| `gtd/models.py` | `ClassifyResult`, `QuadrantUpdateRequest` |
| `gtd/engine/prompts.py` | `CLASSIFY_*` prompts + `QUADRANT_CLARIFY_HINTS` |
| `gtd/engine/llm.py` | Route mock to `_mock_classify_response` for classify prompts |
| `gtd/engine/classify.py` | **New** — classify engine with retry |
| `gtd/engine/clarify.py` | Inject quadrant hint into clarify prompt |
| `gtd/channels/api.py` | BackgroundTasks on capture + classify/quadrant APIs |
| `gtd/channels/feishu.py` | Thread classify after capture |
| `bin/gtd` | Sync classify after CLI add |
| `gtd/templates/inbox.html` | Badges, filter, matrix view, drag-drop |
| `tests/test_classify.py` | **New** — classify engine tests |
| `tests/test_clarify_quadrant.py` | **New** — quadrant prompt injection tests |

---

### Task 1: Database Schema + Pydantic Models

**Files:**
- Modify: `gtd/db.py`
- Modify: `gtd/models.py`
- Test: `tests/test_classify.py` (DB section only)

**Interfaces:**
- Produces:
  - `migrate_inbox_quadrant_columns() -> None`
  - `set_inbox_quadrant_classifying(item_id: int) -> None`
  - `set_inbox_quadrant_classified(item_id: int, quadrant: str, reasoning: str, source: str = "ai") -> None`
  - `set_inbox_quadrant_failed(item_id: int) -> None`
  - `update_inbox_quadrant_user(item_id: int, quadrant: str) -> None`
  - `list_inbox(status: str | None = None, limit: int = 50) -> list[dict]` (updated sort)
  - `ClassifyResult`, `QuadrantUpdateRequest` in `gtd/models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_classify.py`:

```python
import os
import tempfile
from pathlib import Path

import pytest

from gtd.db import (
    add_to_inbox,
    get_inbox_item,
    init_db,
    list_inbox,
    migrate_inbox_quadrant_columns,
    set_inbox_quadrant_classified,
    set_inbox_quadrant_failed,
    update_inbox_quadrant_user,
)
from gtd import settings as settings_module


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setattr(settings_module.settings, "db_path", str(db_path))
        init_db()
        migrate_inbox_quadrant_columns()
        yield db_path


def test_new_inbox_item_has_pending_quadrant_status(temp_db):
    item_id = add_to_inbox("买牛奶")
    item = get_inbox_item(item_id)
    assert item["quadrant_status"] == "pending"
    assert item["quadrant"] is None


def test_set_inbox_quadrant_classified(temp_db):
    item_id = add_to_inbox("紧急会议")
    set_inbox_quadrant_classified(item_id, "q1", "今天截止且影响项目", source="ai")
    item = get_inbox_item(item_id)
    assert item["quadrant"] == "q1"
    assert item["quadrant_status"] == "classified"
    assert item["quadrant_reasoning"] == "今天截止且影响项目"
    assert item["quadrant_source"] == "ai"
    assert item["quadrant_classified_at"] is not None


def test_update_inbox_quadrant_user(temp_db):
    item_id = add_to_inbox("读书")
    update_inbox_quadrant_user(item_id, "q2")
    item = get_inbox_item(item_id)
    assert item["quadrant"] == "q2"
    assert item["quadrant_source"] == "user"
    assert item["quadrant_status"] == "classified"


def test_list_inbox_sorts_by_quadrant_priority(temp_db):
    q4 = add_to_inbox("随便看看")
    q1 = add_to_inbox("客户投诉")
    q2 = add_to_inbox("年度规划")
    set_inbox_quadrant_classified(q4, "q4", "不重要")
    set_inbox_quadrant_classified(q1, "q1", "紧急")
    set_inbox_quadrant_classified(q2, "q2", "重要")
    ids = [item["id"] for item in list_inbox()]
    assert ids.index(q1) < ids.index(q2) < ids.index(q4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_classify.py -v`

Expected: FAIL — `ImportError` or `AttributeError` for missing functions/columns

- [ ] **Step 3: Implement migration + CRUD**

In `gtd/db.py`, add after `init_db()` closes:

```python
_QUADRANT_COLUMNS = [
    ("quadrant", "TEXT"),
    ("quadrant_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("quadrant_reasoning", "TEXT"),
    ("quadrant_source", "TEXT"),
    ("quadrant_classified_at", "TEXT"),
]


def migrate_inbox_quadrant_columns() -> None:
    conn = get_conn()
    for col, col_type in _QUADRANT_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE inbox_items ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()
```

Call `migrate_inbox_quadrant_columns()` at end of `init_db()`.

Add CRUD functions:

```python
VALID_QUADRANTS = {"q1", "q2", "q3", "q4"}

_QUADRANT_SORT_SQL = """
    CASE quadrant
        WHEN 'q1' THEN 0
        WHEN 'q2' THEN 1
        WHEN 'q3' THEN 2
        WHEN 'q4' THEN 3
        ELSE 4
    END,
    created_at DESC
"""


def set_inbox_quadrant_classifying(item_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE inbox_items SET quadrant_status = 'classifying' WHERE id = ?",
        (item_id,),
    )
    conn.commit()
    conn.close()


def set_inbox_quadrant_classified(
    item_id: int, quadrant: str, reasoning: str, source: str = "ai"
) -> None:
    if quadrant not in VALID_QUADRANTS:
        raise ValueError(f"invalid quadrant: {quadrant}")
    conn = get_conn()
    conn.execute(
        """UPDATE inbox_items
           SET quadrant = ?, quadrant_status = 'classified',
               quadrant_reasoning = ?, quadrant_source = ?,
               quadrant_classified_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (quadrant, reasoning, source, item_id),
    )
    conn.commit()
    conn.close()


def set_inbox_quadrant_failed(item_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE inbox_items
           SET quadrant = NULL, quadrant_status = 'failed'
           WHERE id = ?""",
        (item_id,),
    )
    conn.commit()
    conn.close()


def update_inbox_quadrant_user(item_id: int, quadrant: str) -> None:
    if quadrant not in VALID_QUADRANTS:
        raise ValueError(f"invalid quadrant: {quadrant}")
    conn = get_conn()
    conn.execute(
        """UPDATE inbox_items
           SET quadrant = ?, quadrant_status = 'classified',
               quadrant_source = 'user',
               quadrant_classified_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (quadrant, item_id),
    )
    conn.commit()
    conn.close()
```

Update `list_inbox()` non-status query to:

```python
rows = conn.execute(
    f"""SELECT * FROM inbox_items
        WHERE archived_at IS NULL
        ORDER BY {_QUADRANT_SORT_SQL}
        LIMIT ?""",
    (limit,),
).fetchall()
```

In `gtd/models.py` append:

```python
class ClassifyResult(BaseModel):
    quadrant: str
    reasoning: str


class QuadrantUpdateRequest(BaseModel):
    quadrant: str
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_classify.py -v`

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add gtd/db.py gtd/models.py tests/test_classify.py
git commit -m "feat: add inbox quadrant schema and CRUD"
```

---

### Task 2: Classification Prompts + Mock LLM

**Files:**
- Modify: `gtd/engine/prompts.py`
- Modify: `gtd/engine/llm.py`
- Test: `tests/test_classify.py` (mock section)

**Interfaces:**
- Produces:
  - `CLASSIFY_SYSTEM_PROMPT: str`
  - `CLASSIFY_USER_PROMPT: str` (template with `{raw_text}`)
  - `QUADRANT_CLARIFY_HINTS: dict[str, str]` — keys `q1`..`q4`
  - `_mock_classify_response(user_prompt: str) -> dict` — returns `{"quadrant": "q1", "reasoning": "..."}`
  - `call_llm()` routes to `_mock_classify_response` when system prompt contains `"艾森豪威尔"`

- [ ] **Step 1: Write failing mock tests**

Append to `tests/test_classify.py`:

```python
from gtd.engine.llm import _mock_classify_response


def test_mock_classify_q1_urgent_important():
    r = _mock_classify_response("请分类以下事项：\n客户投诉需今天回复")
    assert r["quadrant"] == "q1"


def test_mock_classify_q3_delegate():
    r = _mock_classify_response("请分类以下事项：\n让助理打印文件")
    assert r["quadrant"] == "q3"


def test_mock_classify_q4_low_priority():
    r = _mock_classify_response("请分类以下事项：\n随便看看新闻")
    assert r["quadrant"] == "q4"


def test_mock_classify_q2_default():
    r = _mock_classify_response("请分类以下事项：\n学习 Python")
    assert r["quadrant"] == "q2"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/pytest tests/test_classify.py::test_mock_classify_q1_urgent_important -v`

- [ ] **Step 3: Add prompts**

In `gtd/engine/prompts.py`:

```python
CLASSIFY_SYSTEM_PROMPT = """你是一个艾森豪威尔矩阵（四象限）分类引擎。

根据事项的紧急性和重要性，将其归入四象限之一：
- q1：紧急且重要（立即处理）
- q2：重要不紧急（计划安排）
- q3：紧急不重要（委派他人）
- q4：不重要不紧急（考虑放弃）

判断规则：
1. 「紧急」= 有明确近期截止、他人等待、或不做会产生明显负面后果
2. 「重要」= 对目标、关系、健康、工作成果有实质影响
3. 只输出 JSON，不要 Markdown 代码块

输出格式：
{
  "quadrant": "q1",
  "reasoning": "一句话说明判断依据"
}"""

CLASSIFY_USER_PROMPT = """请分类以下事项：

{raw_text}"""

QUADRANT_CLARIFY_HINTS = {
    "q1": (
        "【象限策略：Q1 立即处理】优先判断两分钟内能否完成；"
        "倾向 next_actions 或 done_log；强调立即可执行的物理动作。"
    ),
    "q2": (
        "【象限策略：Q2 计划安排】正常 GTD 澄清；"
        "倾向 projects 或 next_actions；建议排期或 defer。"
    ),
    "q3": (
        "【象限策略：Q3 委派他人】优先判断 delegate_to；"
        "倾向 waiting_for；追问谁来做。"
    ),
    "q4": (
        "【象限策略：Q4 考虑放弃】倾向 trash 或 someday_maybe；"
        "追问是否真的需要做。"
    ),
}
```

- [ ] **Step 4: Add `_mock_classify_response` and routing**

In `gtd/engine/llm.py`, update `call_llm`:

```python
def call_llm(system_prompt: str, user_prompt: str) -> dict:
    model = settings.llm_model
    if not model or model == "mock":
        if "艾森豪威尔" in system_prompt:
            return _mock_classify_response(user_prompt)
        return _mock_response(user_prompt)
    return _call_litellm(model, system_prompt, user_prompt)
```

Add before `_mock_response`:

```python
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
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_classify.py -v`

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add gtd/engine/prompts.py gtd/engine/llm.py tests/test_classify.py
git commit -m "feat: add quadrant classify prompts and mock heuristics"
```

---

### Task 3: Classify Engine with Retry

**Files:**
- Create: `gtd/engine/classify.py`
- Test: `tests/test_classify.py` (engine section)

**Interfaces:**
- Consumes: DB functions from Task 1, `call_llm`, `CLASSIFY_*` prompts
- Produces: `classify(inbox_id: int) -> dict` returning `{"quadrant": str, "reasoning": str}`

- [ ] **Step 1: Write failing engine tests**

Append to `tests/test_classify.py`:

```python
from unittest.mock import patch

from gtd.engine.classify import classify, _validate_classify_result


def test_validate_classify_result_accepts_valid():
    assert _validate_classify_result({"quadrant": "q2", "reasoning": "ok"}) == {
        "quadrant": "q2",
        "reasoning": "ok",
    }


def test_validate_classify_result_rejects_bad_quadrant():
    result = _validate_classify_result({"quadrant": "q9", "reasoning": "x"})
    assert result["quadrant"] == "q2"  # fallback default


def test_classify_success(temp_db):
    item_id = add_to_inbox("学习 Python")
    with patch("gtd.engine.classify.call_llm", return_value={"quadrant": "q2", "reasoning": "重要不紧急"}):
        result = classify(item_id)
    assert result["quadrant"] == "q2"
    item = get_inbox_item(item_id)
    assert item["quadrant_status"] == "classified"


def test_classify_retries_then_fails(temp_db):
    item_id = add_to_inbox("失败测试")
    with patch("gtd.engine.classify.call_llm", side_effect=RuntimeError("api down")):
        with pytest.raises(RuntimeError):
            classify(item_id)
    item = get_inbox_item(item_id)
    assert item["quadrant_status"] == "failed"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/pytest tests/test_classify.py::test_classify_success -v`

- [ ] **Step 3: Implement `gtd/engine/classify.py`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_classify.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add gtd/engine/classify.py tests/test_classify.py
git commit -m "feat: add classify engine with 3-attempt retry"
```

---

### Task 4: Quadrant-Aware Clarify

**Files:**
- Modify: `gtd/engine/clarify.py`
- Create: `tests/test_clarify_quadrant.py`

**Interfaces:**
- Consumes: `QUADRANT_CLARIFY_HINTS` from `gtd/engine/prompts.py`
- Produces: `build_clarify_system_prompt(quadrant: str | None) -> str` (can be module-level function in `clarify.py`)

- [ ] **Step 1: Write failing test**

Create `tests/test_clarify_quadrant.py`:

```python
from gtd.engine.clarify import build_clarify_system_prompt
from gtd.engine.prompts import CLARIFY_SYSTEM_PROMPT


def test_build_clarify_prompt_without_quadrant():
    prompt = build_clarify_system_prompt(None)
    assert prompt == CLARIFY_SYSTEM_PROMPT


def test_build_clarify_prompt_q1_injects_hint():
    prompt = build_clarify_system_prompt("q1")
    assert "Q1 立即处理" in prompt
    assert CLARIFY_SYSTEM_PROMPT in prompt


def test_build_clarify_prompt_failed_quadrant_no_hint():
    prompt = build_clarify_system_prompt(None)
    assert "象限策略" not in prompt
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `.venv/bin/pytest tests/test_clarify_quadrant.py -v`

- [ ] **Step 3: Implement prompt builder + wire into clarify()**

In `gtd/engine/clarify.py`:

```python
from gtd.engine.prompts import CLARIFY_SYSTEM_PROMPT, CLARIFY_USER_PROMPT, QUADRANT_CLARIFY_HINTS


def build_clarify_system_prompt(quadrant: str | None) -> str:
    if quadrant and quadrant in QUADRANT_CLARIFY_HINTS:
        return CLARIFY_SYSTEM_PROMPT + "\n\n" + QUADRANT_CLARIFY_HINTS[quadrant]
    return CLARIFY_SYSTEM_PROMPT
```

Update `clarify()`:

```python
def clarify(inbox_id: int) -> dict:
    item = get_inbox_item(inbox_id)
    if not item:
        raise ValueError(f"Inbox item {inbox_id} not found")

    set_inbox_clarifying(inbox_id)
    system_prompt = build_clarify_system_prompt(item.get("quadrant"))
    user_prompt = CLARIFY_USER_PROMPT.format(raw_text=item["raw_text"])
    result = call_llm(system_prompt, user_prompt)
    ...
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_clarify_quadrant.py tests/test_classify.py -v`

Expected: all PASS; existing `tests/test_llm.py` still passes

- [ ] **Step 5: Commit**

```bash
git add gtd/engine/clarify.py tests/test_clarify_quadrant.py
git commit -m "feat: inject quadrant strategy into clarify prompt"
```

---

### Task 5: API Endpoints + Background Classification

**Files:**
- Modify: `gtd/channels/api.py`
- Test: `tests/test_classify.py` (API section)

**Interfaces:**
- Consumes: `classify()` from Task 3, `update_inbox_quadrant_user` from Task 1
- Produces:
  - `POST /api/inbox` triggers `background_tasks.add_task(classify, rid)`
  - `POST /api/inbox/{id}/classify`
  - `PATCH /api/inbox/{id}/quadrant`

- [ ] **Step 1: Write failing API test**

Append to `tests/test_classify.py`:

```python
from fastapi.testclient import TestClient

from gtd.main import app


@pytest.fixture()
def client(temp_db):
    return TestClient(app)


def test_api_add_inbox_triggers_classify(client):
    with patch("gtd.channels.api.classify") as mock_classify:
        resp = client.post("/api/inbox", json={"text": "测试", "source": "web"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_classify.assert_called_once()


def test_api_patch_quadrant(client, temp_db):
    item_id = add_to_inbox("读书")
    resp = client.patch(f"/api/inbox/{item_id}/quadrant", json={"quadrant": "q2"})
    assert resp.status_code == 200
    assert get_inbox_item(item_id)["quadrant"] == "q2"
    assert get_inbox_item(item_id)["quadrant_source"] == "user"


def test_api_reclassify(client, temp_db):
    item_id = add_to_inbox("紧急事项")
    with patch("gtd.engine.classify.classify", return_value={"quadrant": "q1", "reasoning": "紧急"}):
        resp = client.post(f"/api/inbox/{item_id}/classify")
    assert resp.status_code == 200
    assert resp.json()["result"]["quadrant"] == "q1"
```

Note: For `test_api_add_inbox_triggers_classify`, patch at `gtd.channels.api.classify` after importing classify there.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/pytest tests/test_classify.py::test_api_patch_quadrant -v`

- [ ] **Step 3: Implement API changes**

In `gtd/channels/api.py`:

```python
from fastapi import APIRouter, HTTPException, BackgroundTasks
from gtd.engine.classify import classify
from gtd.db import update_inbox_quadrant_user
from gtd.models import QuadrantUpdateRequest

VALID_QUADRANTS = {"q1", "q2", "q3", "q4"}


@router.post("/api/inbox")
def api_add_inbox(req: InboxAddRequest, background_tasks: BackgroundTasks):
    if not req.text.strip():
        return {"ok": False, "error": "text is empty"}
    rid = add_to_inbox(req.text.strip(), req.source)
    background_tasks.add_task(classify, rid)
    return {"ok": True, "id": rid, "status": "captured"}


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
```

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add gtd/channels/api.py tests/test_classify.py
git commit -m "feat: add classify API endpoints and background capture trigger"
```

---

### Task 6: Feishu + CLI Capture Integration

**Files:**
- Modify: `gtd/channels/feishu.py`
- Modify: `bin/gtd`

**Interfaces:**
- Consumes: `classify(inbox_id: int)` from Task 3

- [ ] **Step 1: Update Feishu handler**

In `gtd/channels/feishu.py`:

```python
import threading
from gtd.engine.classify import classify

def _on_message(event: P2ImMessageReceiveV1) -> None:
    ...
    item_id = add_to_inbox(text, source="feishu", source_meta=meta)
    _send_reply(msg.chat_id, "已收集 ✓")
    threading.Thread(
        target=classify, args=(item_id,), daemon=True, name=f"classify-{item_id}"
    ).start()
```

Change `add_to_inbox` call to capture returned `item_id`.

- [ ] **Step 2: Update CLI `cmd_add`**

In `bin/gtd`:

```python
from gtd.engine.classify import classify

def cmd_add(args):
    text = " ".join(args.text) if isinstance(args.text, list) else args.text
    rid = add_to_inbox(text, source="cli")
    try:
        result = classify(rid)
        q_labels = {"q1": "Q1 立即处理", "q2": "Q2 计划安排", "q3": "Q3 委派他人", "q4": "Q4 考虑放弃"}
        label = q_labels.get(result["quadrant"], result["quadrant"])
        print(f"[{rid}] ✓ 已收集: {text}")
        print(f"     象限: {label} — {result['reasoning']}")
    except Exception:
        print(f"[{rid}] ✓ 已收集: {text}")
        print("     象限: 分类失败（可稍后重试）")
```

- [ ] **Step 3: Manual smoke test**

Run:
```bash
.venv/bin/python bin/gtd add "客户投诉需今天回复"
.venv/bin/python bin/gtd inbox
```

Expected: item shows with Q1 classification in CLI output

- [ ] **Step 4: Commit**

```bash
git add gtd/channels/feishu.py bin/gtd
git commit -m "feat: trigger quadrant classify from Feishu and CLI capture"
```

---

### Task 7: Inbox UI — List View (Badges, Filter, Poll)

**Files:**
- Modify: `gtd/templates/inbox.html`
- Modify: `gtd/channels/api.py` (page route passes quadrant data — already in `list_inbox`)

**Interfaces:**
- Consumes: quadrant fields on inbox items from API/SSR

- [ ] **Step 1: Add CSS for quadrant badges**

In `inbox.html` `{% block extra_css %}`:

```css
.quadrant-badge {
  font-size: 0.72em; font-weight: 700; padding: 3px 8px; border-radius: 999px;
  cursor: pointer; border: none; color: #fff;
}
.quadrant-q1 { background: #dc2626; }
.quadrant-q2 { background: #2563eb; }
.quadrant-q3 { background: #ea580c; }
.quadrant-q4 { background: #6b7280; }
.quadrant-pending { background: #9ca3af; color: #fff; }
.quadrant-classifying { background: #f59e0b; color: #1f2937; }
.quadrant-failed { background: #fecaca; color: #991b1b; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.filter-btn {
  font-size: 0.8em; padding: 5px 10px; border-radius: 999px;
  border: 1px solid #d1d5db; background: #fff; cursor: pointer;
}
.filter-btn.active { background: #2563eb; color: #fff; border-color: #2563eb; }
.view-toggle { margin-left: auto; }
```

- [ ] **Step 2: Add filter bar + quadrant badges to item template**

Above item list, add filter bar and view toggle placeholder:

```html
<div class="toolbar" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
  <div class="filter-bar" id="quadrant-filter">
    <button class="filter-btn active" data-filter="all">全部</button>
    <button class="filter-btn" data-filter="q1">Q1 立即处理</button>
    <button class="filter-btn" data-filter="q2">Q2 计划安排</button>
    <button class="filter-btn" data-filter="q3">Q3 委派他人</button>
    <button class="filter-btn" data-filter="q4">Q4 考虑放弃</button>
    <button class="filter-btn" data-filter="unclassified">未分类</button>
  </div>
  <button class="filter-btn view-toggle" id="view-toggle-btn" onclick="toggleView()">切换到矩阵</button>
</div>
```

Update each item `div` data attributes:

```html
data-quadrant="{{ item.quadrant or '' }}"
data-quadrant-status="{{ item.quadrant_status or 'pending' }}"
data-quadrant-reasoning="{{ (item.quadrant_reasoning or '')|e }}"
```

Add badge in `.item-top`:

```html
<span class="quadrant-slot" id="quadrant-badge-{{ item.id }}"></span>
```

- [ ] **Step 3: Add JS for badge rendering, filter, adaptive poll**

```javascript
const QUADRANT_LABELS = {
  q1: 'Q1 立即处理', q2: 'Q2 计划安排', q3: 'Q3 委派他人', q4: 'Q4 考虑放弃'
};

function renderQuadrantBadge(itemEl) {
  const status = itemEl.dataset.quadrantStatus || 'pending';
  const quadrant = itemEl.dataset.quadrant || '';
  const slot = itemEl.querySelector('.quadrant-slot');
  if (!slot) return;
  let html = '';
  if (status === 'classifying') {
    html = '<span class="quadrant-badge quadrant-classifying">分类中…</span>';
  } else if (status === 'failed') {
    html = '<button class="quadrant-badge quadrant-failed" onclick="retryClassify(' + itemEl.dataset.itemId + ')">分类失败 · 重试</button>';
  } else if (quadrant) {
    html = '<button class="quadrant-badge quadrant-' + quadrant + '" onclick="cycleQuadrant(' + itemEl.dataset.itemId + ', \'' + quadrant + '\')">' + (QUADRANT_LABELS[quadrant] || quadrant) + '</button>';
  } else {
    html = '<span class="quadrant-badge quadrant-pending">未分类</span>';
  }
  slot.innerHTML = html;
}

function cycleQuadrant(itemId, current) {
  const order = ['q1', 'q2', 'q3', 'q4'];
  const next = order[(order.indexOf(current) + 1) % order.length];
  patchQuadrant(itemId, next);
}

function patchQuadrant(itemId, quadrant) {
  fetch('/api/inbox/' + itemId + '/quadrant', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({quadrant: quadrant})
  }).then(() => location.reload());
}

function retryClassify(itemId) {
  fetch('/api/inbox/' + itemId + '/classify', {method: 'POST'})
    .then(() => location.reload());
}

document.querySelectorAll('.item[data-item-id]').forEach(renderQuadrantBadge);

// Filter
document.querySelectorAll('#quadrant-filter .filter-btn[data-filter]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#quadrant-filter .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const f = btn.dataset.filter;
    document.querySelectorAll('.item[data-item-id]').forEach(el => {
      const q = el.dataset.quadrant || '';
      const st = el.dataset.quadrantStatus || '';
      const show = f === 'all'
        || (f === 'unclassified' && !q)
        || (f === q);
      el.style.display = show ? '' : 'none';
    });
  });
});

// Adaptive poll
function getPollInterval() {
  const classifying = document.querySelector('[data-quadrant-status="classifying"]');
  return classifying ? 5000 : 30000;
}
function scheduleReload() {
  setTimeout(() => location.reload(), getPollInterval());
}
scheduleReload();
```

Remove old fixed 30s reload at bottom of template if present.

- [ ] **Step 4: Show quadrant in clarify modal**

In `openActionCard()`, append quadrant chip to `#card-item-meta`:

```javascript
const quadrant = item.dataset.quadrant || '';
const qStatus = item.dataset.quadrantStatus || '';
const reasoning = item.dataset.quadrantReasoning || '';
if (quadrant) {
  chips.push('<span class="meta-chip">' + escapeHtml(QUADRANT_LABELS[quadrant] || quadrant) + '</span>');
}
if (reasoning) {
  chips.push('<span class="meta-chip">象限依据：' + escapeHtml(reasoning) + '</span>');
}
```

- [ ] **Step 5: Manual UI smoke test**

Start server, add item via form, verify badge appears after poll.

- [ ] **Step 6: Commit**

```bash
git add gtd/templates/inbox.html
git commit -m "feat: inbox list view with quadrant badges and filter"
```

---

### Task 8: Inbox UI — Matrix View + Drag-Drop

**Files:**
- Modify: `gtd/templates/inbox.html`

- [ ] **Step 1: Add matrix CSS + HTML container**

```css
.matrix-view { display: none; }
.matrix-view.active { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.list-view.hidden { display: none; }
.matrix-cell {
  background: #fff; border-radius: 8px; padding: 10px; min-height: 120px;
  border: 2px solid #e5e7eb;
}
.matrix-cell.q1 { border-color: #fca5a5; }
.matrix-cell.q2 { border-color: #93c5fd; }
.matrix-cell.q3 { border-color: #fdba74; }
.matrix-cell.q4 { border-color: #d1d5db; }
.matrix-cell h3 { font-size: 0.85em; margin-bottom: 8px; }
.matrix-card {
  background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px;
  padding: 8px; margin-bottom: 6px; font-size: 0.85em; cursor: grab;
}
.matrix-unclassified { margin-top: 12px; background: #fff; border-radius: 8px; padding: 10px; }
```

Wrap existing list items in `<div id="list-view" class="list-view">` and add:

```html
<div id="matrix-view" class="matrix-view">
  <div class="matrix-cell q1" data-quadrant="q1" ondragover="allowDrop(event)" ondrop="dropCard(event,'q1')"><h3>Q1 立即处理</h3><div class="matrix-drop"></div></div>
  <div class="matrix-cell q2" data-quadrant="q2" ondragover="allowDrop(event)" ondrop="dropCard(event,'q2')"><h3>Q2 计划安排</h3><div class="matrix-drop"></div></div>
  <div class="matrix-cell q3" data-quadrant="q3" ondragover="allowDrop(event)" ondrop="dropCard(event,'q3')"><h3>Q3 委派他人</h3><div class="matrix-drop"></div></div>
  <div class="matrix-cell q4" data-quadrant="q4" ondragover="allowDrop(event)" ondrop="dropCard(event,'q4')"><h3>Q4 考虑放弃</h3><div class="matrix-drop"></div></div>
</div>
<div id="matrix-unclassified" class="matrix-unclassified" style="display:none;"><h3>待分类</h3><div id="matrix-unclassified-list"></div></div>
```

- [ ] **Step 2: Add matrix JS**

```javascript
let currentView = 'list';

function toggleView() {
  currentView = currentView === 'list' ? 'matrix' : 'list';
  document.getElementById('list-view').classList.toggle('hidden', currentView === 'matrix');
  document.getElementById('matrix-view').classList.toggle('active', currentView === 'matrix');
  document.getElementById('view-toggle-btn').textContent =
    currentView === 'list' ? '切换到矩阵' : '切换到列表';
  if (currentView === 'matrix') buildMatrix();
}

function buildMatrix() {
  ['q1','q2','q3','q4'].forEach(q => {
    const cell = document.querySelector('.matrix-cell[data-quadrant="' + q + '"] .matrix-drop');
    if (cell) cell.innerHTML = '';
  });
  const unclassified = document.getElementById('matrix-unclassified-list');
  unclassified.innerHTML = '';
  let hasUnclassified = false;

  document.querySelectorAll('.item[data-item-id]').forEach(el => {
    const id = el.dataset.itemId;
    const text = el.dataset.rawText || '';
    const q = el.dataset.quadrant || '';
    const st = el.dataset.quadrantStatus || '';
    const card = document.createElement('div');
    card.className = 'matrix-card';
    card.draggable = true;
    card.dataset.itemId = id;
    card.textContent = text;
    card.ondragstart = (e) => { e.dataTransfer.setData('text/plain', id); };

    if (q && st === 'classified') {
      const drop = document.querySelector('.matrix-cell[data-quadrant="' + q + '"] .matrix-drop');
      if (drop) drop.appendChild(card);
    } else {
      unclassified.appendChild(card);
      hasUnclassified = true;
    }
  });
  document.getElementById('matrix-unclassified').style.display = hasUnclassified ? '' : 'none';
}

function allowDrop(e) { e.preventDefault(); }

function dropCard(e, quadrant) {
  e.preventDefault();
  const itemId = e.dataTransfer.getData('text/plain');
  if (itemId) patchQuadrant(itemId, quadrant);
}
```

- [ ] **Step 3: Manual smoke test**

Verify drag between quadrants updates badge after reload.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add gtd/templates/inbox.html
git commit -m "feat: inbox matrix view with drag-drop quadrant update"
```

---

## Self-Review

**Spec coverage:**
- ✅ Parallel classify + quadrant-aware clarify → Tasks 3, 4
- ✅ Background async on capture → Task 5
- ✅ Feishu thread + CLI sync → Task 6
- ✅ User override PATCH → Task 5
- ✅ Dual list/matrix UI → Tasks 7, 8
- ✅ 3-attempt retry + failed state → Task 3
- ✅ Mock offline → Task 2
- ✅ DB migration for existing rows → Task 1
- ✅ Clarify modal quadrant display → Task 7 Step 4
- ✅ Poll 5s/30s → Task 7 Step 3

**Placeholder scan:** No TBD/TODO found.

**Type consistency:** `classify(inbox_id: int) -> dict` used consistently; quadrant values `q1`–`q4` match across DB, API, UI.
