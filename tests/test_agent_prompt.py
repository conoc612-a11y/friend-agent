"""답 생성 지시문의 조건부 규칙(근거 없는 사실 금지) 검사."""
from datetime import datetime, timedelta, timezone

from app.agent import system_prompt
from app.persona import DEFAULT_CHARACTER

KST = timezone(timedelta(hours=9))


def test_tool_failed_or_missing_turn_gets_guard():
    prompt = system_prompt(DEFAULT_CHARACTER, datetime.now(KST), "서울", [], None, [], tools_ok=False)
    assert "근거 없는 사실 금지" in prompt


def test_tool_success_turn_has_no_guard():
    prompt = system_prompt(DEFAULT_CHARACTER, datetime.now(KST), "서울", [], None, ["서울 날씨: 맑음"], tools_ok=True)
    assert "근거 없는 사실 금지" not in prompt


def test_default_call_has_no_guard():
    prompt = system_prompt(DEFAULT_CHARACTER, datetime.now(KST), "서울", [], None, [])
    assert "근거 없는 사실 금지" not in prompt
