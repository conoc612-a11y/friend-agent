"""토스페이먼츠 결제 · 크레딧 원장 검증 (가짜 Toss + 메모리 저장소).

payment.py 는 아직 스텁이다: 주문 저장/상태 확인(멱등 1겹), 금액 비교, 서버측 크레딧 계산이
구현되어 있지 않다. 그 부분은 skip 표시로 남겨 두고, 실제로 동작하는 것(승인 호출, 참조 중복
방지)만 검증한다.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app, store

client = TestClient(app)


def _setup_user():
    store.add_credit("local-user", 100, "test-init", "test:init")
    return "local-user"


def test_order_and_confirm():
    _setup_user()
    order_resp = client.post("/api/orders", json={"product_id": "credits_100"})
    assert order_resp.status_code == 200
    o = order_resp.json()
    assert o["amount"] == 1000
    assert o["credits"] == 100
    before = store.get_balance("local-user")
    with patch("app.payment.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "done", "paymentKey": "test_pk_" + o["id"]}
        r = client.post("/api/payments/confirm", json={
            "orderId": o["id"], "amount": o["amount"], "paymentKey": "test_pk_" + o["id"],
            "credits": o["credits"],  # 지금은 화면이 보낸 값을 쓴다 (서버가 상품표에서 찾도록 고칠 자리)
        })
        assert r.status_code == 200
        assert store.get_balance("local-user") == before + 100
        # 멱등키 헤더가 주문번호로 붙는다
        headers = mock_post.call_args.kwargs.get("headers", {})
        assert headers.get("Idempotency-Key") == o["id"]


def test_same_order_credits_only_once():
    """같은 주문으로 두 번 승인돼도 원장(order:<주문번호>)은 한 줄, 잔액도 한 번만 는다."""
    _setup_user()
    o = client.post("/api/orders", json={"product_id": "credits_100"}).json()
    before = store.get_balance("local-user")
    with patch("app.payment.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "done"}
        for _ in range(2):
            client.post("/api/payments/confirm", json={
                "orderId": o["id"], "amount": o["amount"], "paymentKey": "test_pk",
                "credits": o["credits"],
            })
    assert store.get_balance("local-user") == before + 100
    rows = [c for c in store.recent_credits("local-user", 50) if c["ref"] == f"order:{o['id']}"]
    assert len(rows) == 1


@pytest.mark.skip(reason="주문 저장·상태 확인(멱등 1겹)이 붙으면 구현: 같은 승인 요청 2번 → 토스 호출 1번")
def test_confirm_calls_toss_once():
    pass


@pytest.mark.skip(reason="주문 조회(get_order)가 붙으면 구현: 금액이 다르면 400 + 토스 호출 0번")
def test_confirm_amount_mismatch():
    pass


@pytest.mark.skip(reason="사용자별 주문 조회가 붙으면 구현: 다른 사용자의 주문 → 404")
def test_confirm_wrong_user():
    pass
