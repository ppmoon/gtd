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


from unittest.mock import patch

from gtd.engine.classify import classify, _validate_classify_result


def test_validate_classify_result_accepts_valid():
    assert _validate_classify_result({"quadrant": "q2", "reasoning": "ok"}) == {
        "quadrant": "q2",
        "reasoning": "ok",
    }


def test_validate_classify_result_rejects_bad_quadrant():
    result = _validate_classify_result({"quadrant": "q9", "reasoning": "x"})
    assert result["quadrant"] == "q2"


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
