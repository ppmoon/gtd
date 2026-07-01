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
