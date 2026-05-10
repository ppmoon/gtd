import json
import sqlite3
from pathlib import Path

from gtd.settings import settings


def get_db_path() -> Path:
    p = Path(settings.db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inbox_items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text            TEXT NOT NULL,
            source              TEXT NOT NULL DEFAULT 'manual',
            source_meta         TEXT,
            status              TEXT NOT NULL DEFAULT 'captured',
            clarify_result_json TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            clarified_at        TEXT,
            archived_at         TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            title                 TEXT NOT NULL,
            desired_outcome       TEXT,
            status                TEXT NOT NULL DEFAULT 'active',
            review_status         TEXT NOT NULL DEFAULT 'ok',
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            last_reviewed_at      TEXT,
            completed_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS actions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            title                 TEXT NOT NULL,
            notes                 TEXT,
            project_id            INTEGER REFERENCES projects(id),
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            status                TEXT NOT NULL DEFAULT 'next',
            context_tag           TEXT,
            energy_level          TEXT,
            estimated_minutes     INTEGER,
            due_at                TEXT,
            defer_until           TEXT,
            is_calendar_required  INTEGER NOT NULL DEFAULT 0,
            created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            completed_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS waiting_for (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            item                  TEXT NOT NULL,
            notes                 TEXT,
            person                TEXT NOT NULL DEFAULT '',
            project_id            INTEGER REFERENCES projects(id),
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            status                TEXT NOT NULL DEFAULT 'waiting',
            follow_up_at          TEXT,
            created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            resolved_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS someday_maybe (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            item                  TEXT NOT NULL,
            category              TEXT,
            notes                 TEXT,
            status                TEXT NOT NULL DEFAULT 'active',
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            reviewed_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS reference_items (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            title                 TEXT NOT NULL,
            content               TEXT NOT NULL DEFAULT '',
            source                TEXT,
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS done_logs (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            title                 TEXT NOT NULL,
            source_inbox_item_id  INTEGER REFERENCES inbox_items(id),
            source_action_id      INTEGER REFERENCES actions(id),
            completed_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            completion_type       TEXT NOT NULL DEFAULT 'manual_done'
        );

        CREATE TABLE IF NOT EXISTS weekly_reviews (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            status          TEXT NOT NULL DEFAULT 'in_progress',
            started_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            completed_at    TEXT,
            summary         TEXT,
            issues_found_json TEXT
        );

        CREATE TABLE IF NOT EXISTS weekly_review_steps (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id     INTEGER NOT NULL REFERENCES weekly_reviews(id),
            step_key      TEXT NOT NULL,
            step_order    INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            user_notes    TEXT,
            system_notes  TEXT,
            completed_at  TEXT
        );
    """)
    conn.commit()
    conn.close()


# ── Inbox ──────────────────────────────────────────────

def add_to_inbox(raw_text: str, source: str = "manual", source_meta: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inbox_items (raw_text, source, source_meta) VALUES (?, ?, ?)",
        (raw_text, source, source_meta),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_inbox_item(item_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_inbox(status: str | None = None, limit: int = 50) -> list[dict]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM inbox_items WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM inbox_items WHERE archived_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def inbox_count(status: str | None = None) -> int:
    conn = get_conn()
    if status:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM inbox_items WHERE status = ?", (status,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM inbox_items WHERE archived_at IS NULL"
        ).fetchone()
    conn.close()
    return row["cnt"]  # type: ignore[no-any-return]


def set_inbox_clarifying(item_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE inbox_items SET status = 'clarifying' WHERE id = ?", (item_id,)
    )
    conn.commit()
    conn.close()


def archive_inbox_item(item_id: int, status: str = "routed") -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE inbox_items SET status = ?, archived_at = datetime('now', 'localtime') WHERE id = ?",
        (status, item_id),
    )
    conn.commit()
    conn.close()


def set_inbox_clarified(item_id: int, clarify_result: dict) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE inbox_items
           SET status = 'clarified', clarify_result_json = ?, clarified_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (json.dumps(clarify_result, ensure_ascii=False), item_id),
    )
    conn.commit()
    conn.close()


# ── Projects ───────────────────────────────────────────

def create_project(title: str, desired_outcome: str | None = None,
                   source_inbox_item_id: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO projects (title, desired_outcome, source_inbox_item_id) VALUES (?, ?, ?)",
        (title, desired_outcome, source_inbox_item_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_project(project_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_projects(status: str = "active") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def project_count(status: str = "active") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM projects WHERE status = ?", (status,)
    ).fetchone()
    conn.close()
    return row["cnt"]


def set_project_review_status(project_id: int, review_status: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE projects SET review_status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (review_status, project_id),
    )
    conn.commit()
    conn.close()


# ── Actions ────────────────────────────────────────────

def create_action(title: str, project_id: int | None = None,
                  source_inbox_item_id: int | None = None,
                  context_tag: str | None = None,
                  energy_level: str | None = None,
                  estimated_minutes: int | None = None,
                  due_at: str | None = None,
                  is_calendar_required: bool = False,
                  notes: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO actions
           (title, project_id, source_inbox_item_id, context_tag, energy_level,
            estimated_minutes, due_at, is_calendar_required, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, project_id, source_inbox_item_id, context_tag, energy_level,
         estimated_minutes, due_at, int(is_calendar_required), notes),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_action(action_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_actions(status: str = "next", context_tag: str | None = None,
                 energy_level: str | None = None, max_minutes: int | None = None) -> list[dict]:
    conn = get_conn()
    query = "SELECT * FROM actions WHERE status = ?"
    params: list = [status]
    if context_tag:
        query += " AND context_tag = ?"
        params.append(context_tag)
    if energy_level:
        query += " AND energy_level = ?"
        params.append(energy_level)
    if max_minutes is not None:
        query += " AND estimated_minutes <= ?"
        params.append(max_minutes)
    query += " ORDER BY created_at ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def action_count(status: str = "next") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM actions WHERE status = ?", (status,)
    ).fetchone()
    conn.close()
    return row["cnt"]


def update_action(action_id: int, **kwargs) -> None:
    if not kwargs:
        return
    conn = get_conn()
    sets = [f"{k} = ?" for k in kwargs]
    vals = list(kwargs.values())
    vals.append(action_id)
    conn.execute(
        f"UPDATE actions SET {', '.join(sets)}, updated_at = datetime('now', 'localtime') WHERE id = ?",
        vals,
    )
    conn.commit()
    conn.close()


# ── Waiting For ────────────────────────────────────────

def create_waiting_for(item: str, person: str, project_id: int | None = None,
                       source_inbox_item_id: int | None = None,
                       follow_up_at: str | None = None, notes: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO waiting_for (item, person, project_id, source_inbox_item_id, follow_up_at, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (item, person, project_id, source_inbox_item_id, follow_up_at, notes),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_waiting_for(status: str = "waiting") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM waiting_for WHERE status = ? ORDER BY created_at ASC", (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def waiting_count(status: str = "waiting") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM waiting_for WHERE status = ?", (status,)
    ).fetchone()
    conn.close()
    return row["cnt"]


# ── Someday/Maybe ──────────────────────────────────────

def create_someday(item: str, category: str | None = None, notes: str | None = None,
                   source_inbox_item_id: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO someday_maybe (item, category, notes, source_inbox_item_id) VALUES (?, ?, ?, ?)",
        (item, category, notes, source_inbox_item_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_someday(status: str = "active") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM someday_maybe WHERE status = ? ORDER BY created_at ASC", (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def someday_count(status: str = "active") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM someday_maybe WHERE status = ?", (status,)
    ).fetchone()
    conn.close()
    return row["cnt"]


# ── Reference ──────────────────────────────────────────

def create_reference(title: str, content: str = "", source: str | None = None,
                     source_inbox_item_id: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO reference_items (title, content, source, source_inbox_item_id) VALUES (?, ?, ?, ?)",
        (title, content, source, source_inbox_item_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_reference(limit: int = 200) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM reference_items ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reference_count() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM reference_items").fetchone()
    conn.close()
    return row["cnt"]


# ── Done Log ───────────────────────────────────────────

def create_done_log(title: str, completion_type: str = "manual_done",
                    source_inbox_item_id: int | None = None,
                    source_action_id: int | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO done_logs (title, completion_type, source_inbox_item_id, source_action_id) VALUES (?, ?, ?, ?)",
        (title, completion_type, source_inbox_item_id, source_action_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_done(limit: int = 200) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM done_logs ORDER BY completed_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def done_count() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM done_logs").fetchone()
    conn.close()
    return row["cnt"]


# ── Integrity ──────────────────────────────────────────

def get_integrity_issues() -> list[dict]:
    """Return list of system integrity issues."""
    issues: list[dict] = []
    conn = get_conn()

    # Projects without next actions
    rows = conn.execute("""
        SELECT p.id, p.title FROM projects p
        WHERE p.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.project_id = p.id AND a.status IN ('next', 'in_progress'))
    """).fetchall()
    for r in rows:
        issues.append({"type": "missing_next_action", "project_id": r["id"], "project_title": r["title"]})

    # Waiting For overdue
    rows = conn.execute("""
        SELECT * FROM waiting_for
        WHERE status = 'waiting' AND follow_up_at IS NOT NULL AND follow_up_at < datetime('now', 'localtime')
    """).fetchall()
    for r in rows:
        issues.append({"type": "stale_waiting_for", "waiting_for_id": r["id"], "item": r["item"], "person": r["person"]})

    # Inbox items too long
    rows = conn.execute("""
        SELECT * FROM inbox_items
        WHERE status = 'captured'
          AND created_at < datetime('now', 'localtime', '-3 days')
    """).fetchall()
    for r in rows:
        issues.append({"type": "stale_inbox", "inbox_id": r["id"], "raw_text": r["raw_text"]})

    conn.close()
    return issues


# ── Weekly Review ──────────────────────────────────────

WEEKLY_REVIEW_STEPS = [
    ("empty_inbox", 1, "清空收集箱"),
    ("review_past_calendar", 2, "回顾过去两周日历"),
    ("review_future_calendar", 3, "查看未来两周日历"),
    ("review_projects", 4, "检查项目是否都有下一步行动"),
    ("review_waiting_for", 5, "检查等待事项是否需要跟进"),
    ("review_someday_maybe", 6, "检查 Someday/Maybe 是否要激活"),
    ("collect_open_loops", 7, "补录新的开放回路"),
    ("generate_summary", 8, "生成本周关注摘要"),
]


def create_weekly_review() -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO weekly_reviews DEFAULT VALUES")
    review_id = cur.lastrowid
    for key, order, _label in WEEKLY_REVIEW_STEPS:
        conn.execute(
            "INSERT INTO weekly_review_steps (review_id, step_key, step_order) VALUES (?, ?, ?)",
            (review_id, key, order),
        )
    conn.commit()
    conn.close()
    return review_id


def get_weekly_review(review_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM weekly_reviews WHERE id = ?", (review_id,)).fetchone()
    if not row:
        conn.close()
        return None
    review = dict(row)
    steps = conn.execute(
        "SELECT * FROM weekly_review_steps WHERE review_id = ? ORDER BY step_order", (review_id,)
    ).fetchall()
    review["steps"] = [dict(s) for s in steps]
    conn.close()
    return review


def complete_review_step(review_id: int, step_key: str, user_notes: str | None = None,
                         system_notes: str | None = None) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE weekly_review_steps
           SET status = 'done', user_notes = ?, system_notes = ?, completed_at = datetime('now', 'localtime')
           WHERE review_id = ? AND step_key = ?""",
        (user_notes, system_notes, review_id, step_key),
    )
    conn.commit()
    conn.close()


def complete_weekly_review(review_id: int, summary: str, issues_found: dict) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE weekly_reviews
           SET status = 'completed', completed_at = datetime('now', 'localtime'),
               summary = ?, issues_found_json = ?
           WHERE id = ?""",
        (summary, json.dumps(issues_found, ensure_ascii=False), review_id),
    )
    conn.commit()
    conn.close()
