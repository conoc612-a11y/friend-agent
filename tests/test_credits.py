"""크레딧 원장 단위 기능 검증. 가짜 LLM(fake)과 메모리 저장소로 실행."""
import json
from fastapi.testclient import TestClient

from app.main import app, store
from app.store.base import CREDIT_COSTS

client = TestClient(app)


def chat(message):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines()]


def test_chat_deducts_one_credit():
    # Initialize user and give 10 initial credits
    store._user("local-user")
    store.add_credit("local-user", 10, "test-init", "test:init")
    initial = store.get_balance("local-user")
    chat("내일 날씨 어때?")
    assert store.get_balance("local-user") == initial - 1


def test_recent_credits_shows_history():
    store._user("local-user")
    store.add_credit("local-user", 10, "test-init", "test:init")
    chat("안녕! 아침이요")
    history = store.recent_credits("local-user", 10)
    # Ledger entries should include the deduction
    assert any(h["reason"] == "메시지 전송" for h in history)


def test_insufficient_credits_402():
    # Initialize user and set balance directly to 0
    store._user("local-user")
    if hasattr(store.users.get("local-user"), "get"):
        pass  # user exists
    if hasattr(store, "users"):
        store.users.setdefault("local-user", store._user("local-user"))
        store.users["local-user"]["balance"] = 0
        store.users["local-user"]["credits"] = []

    resp = client.post("/api/chat", json={"message": "날씨 어때?"})
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"] == "insufficient_credits"
    assert "balance" in body


def test_ocr_cost_exceeds_message():
    # OCR costs 3 credits per call, which is more than messages' 1 credit
    assert CREDIT_COSTS["ocr"] > CREDIT_COSTS["message"]
