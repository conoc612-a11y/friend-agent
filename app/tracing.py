"""Langfuse 트레이스에 붙는 공통 속성.

계측(app/llm.py, app/agent.py 등)은 기록을 만들고, 이 모듈은 기록에 붙는 속성만 담당한다.
대화 원문은 접근 권한이 걸린 관측 도구(랭퓨즈)에만 남는다. 서버 로그(app/logs.py)에
원문을 남기지 않는 규칙은 그대로 둔다.
"""
import os
from datetime import datetime, timezone, timedelta


def korean_date_str():
    """한국 시간대(UTC+9) 기준 오늘 날짜 YYYY-MM-DD"""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d")


def base_attributes(uid):
    """모든 트레이스에 공통으로 붙는 기본 속성."""
    return {
        "user_id": uid,
        "environment": os.environ.get("ENV", "local"),
        "version": os.environ.get("COMMIT_SHA", "local") or os.environ.get("K_REVISION", "local"),
    }


def turn_attributes(uid, feature, tools=None, confidence=None, decided_by=None, safety_action=None):
    """턴 트레이스에 붙는 속성. propagate_attributes() 에 넘긴다.

    - session_id: 사용자 ID + 한국 날짜(YYYY-MM-DD)로 하루 단위 세션 구성
    - tags: 기능(chat/greeting), 고른 도구 이름, 안전 응답 여부
    - metadata: 커밋, 고른 도구, 확신도, 누가 정했는지, 안전 응답 여부
    """
    base = base_attributes(uid)
    today = korean_date_str()

    tags = ["friend-agent", feature]
    meta = {"commit": base["version"]}

    if tools:
        tool_names = [t["name"] for t in tools]
        tags.extend(tool_names)
        meta["tools"] = tool_names
    if confidence is not None:
        meta["confidence"] = confidence
    if decided_by:
        meta["decided_by"] = decided_by
    if safety_action:
        tags.append("safety")
        meta["safety"] = True

    return {
        **base,
        "session_id": f"{uid}:{today}",
        "tags": tags,
        "metadata": meta,
    }


def greet_attributes(uid):
    """greeting 트레이스용 속성 (도구 선택 정보 없음)."""
    base = base_attributes(uid)
    today = korean_date_str()
    return {
        **base,
        "session_id": f"{uid}:{today}",
        "tags": ["friend-agent", "greeting"],
        "metadata": {"commit": base["version"]},
    }