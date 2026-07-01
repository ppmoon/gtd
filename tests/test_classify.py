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
