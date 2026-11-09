"""토스페이먼츠 결제 승인 호출. MCP 문서 대신 표준 v2 API 사용."""
import base64
import os

import httpx

from app.logs import log

TOSS_URL = "https://api.tosspayments.com/v1/payments/confirm"


def _auth_header(secret_key: str):
    # Toss v2: Basic base64(secret_key:)
    token = base64.b64encode(f"{secret_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def cancel_payment(uid, order_id, amount, payment_key):
    """토스 취소 API + 원장 refund:주문번호 - 줄. 멱등키: refund:주문번호"""
    from app.store.base import make_store
    from app.config import load_settings
    settings = load_settings()
    secret = settings.toss_secret_key or os.environ.get("TOSS_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("TOSS_SECRET_KEY 없음")

    idempotency_key = f"refund:{order_id}"
    cancel_url = f"https://api.tosspayments.com/v1/payments/{payment_key}/cancel"

    try:
        resp = httpx.post(
            cancel_url,
            json={"cancelReason": "user_request"},
            headers={**_auth_header(secret), "Idempotency-Key": idempotency_key},
            timeout=15,
        )
    except Exception as e:
        log("ERROR", "토스 취소 API 연결 실패", error=str(e), order_id=order_id,
            idempotency_key=idempotency_key)
        raise

    log("INFO", "토스 취소 호출", order_id=order_id, idempotency_key=idempotency_key,
        status_code=resp.status_code)

    store = make_store(settings)
    if resp.status_code in (200, 410):
        store.add_credit(uid, -amount, "환불", f"refund:{order_id}")
        return resp.json()
    else:
        raise RuntimeError(f"Toss cancel failed: {resp.status_code}")


def confirm_payment(uid, order_id, product, order_row, store):
    """승인 API 호출 + 멱등성 장치.

    store 는 앱(main.py)이 쓰는 저장소를 그대로 받는다. 여기서 make_store 로 새로 만들면
    메모리 저장소일 때 크레딧이 다른 인스턴스에 쌓여 사라진다.
    """
    from app.config import load_settings
    settings = load_settings()

    secret = settings.toss_secret_key or os.environ.get("TOSS_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("TOSS_SECRET_KEY 없음")

    # 1. 주문 상태 확인은 아직 없다: 주문 저장/조회(store.get_order)가 붙으면 여기서 검사한다

    # 2. 멱등키는 주문 번호 그대로 (같은 주문 → 같은 키)
    idempotency_key = order_id  # 300자 이하, 15일 유효

    # 3. 승인 요청
    payload = {"paymentKey": order_row.get("payment_key"), "orderId": order_id,
               "amount": order_row.get("amount")}
    # (성공 페이지가 보낸 paymentKey와 amount로 실제 승인)

    try:
        resp = httpx.post(
            TOSS_URL,
            json=payload,
            headers={**_auth_header(secret), "Idempotency-Key": idempotency_key},
            timeout=15,
        )
    except Exception as e:
        log("ERROR", "토스 승인 API 연결 실패", error=str(e), order_id=order_id,
            idempotency_key=idempotency_key)
        raise

    # 로그: orderId와 Idempotency-Key만 (시크릿/결제키 전체 제외)
    log("INFO", "토스 승인 호출", order_id=order_id, idempotency_key=idempotency_key,
        status_code=resp.status_code, response_preview=str(resp.json())[:200])

    if resp.status_code == 200:
        # 성공: 원장 +주문 기록 + 주문 완료
        store.add_credit(uid, order_row.get("credits_to_grant", 0), "결제 충전",
                         f"order:{order_id}")
        # orders 상태 업데이트는 DB 함수로
        return resp.json()
    elif resp.status_code == 409:
        # 멱등 처리 중
        log("INFO", "토스 멱등 처리 중", order_id=order_id, idempotency_key=idempotency_key)
        return resp.json()
    else:
        log("ERROR", "토스 승인 실패", order_id=order_id, status=resp.status_code,
            body=str(resp.text)[:300])
        raise RuntimeError(f"Toss approve failed: {resp.status_code}")
