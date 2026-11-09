"""AI 친구 에이전트 서비스.

화면(/)과 API를 FastAPI 하나가 함께 제공한다.
- /health, /version: 서비스 상태와 배포 버전
- /api/config: 화면이 쓰는 설정(로그인 방식, 개발용 패널 표시 여부)
- /api/character, /api/settings: 캐릭터와 사용자 설정
- /api/chat: 메시지 한 턴. 결과를 이벤트(JSON 한 줄씩)로 스트리밍한다
- /api/greeting: 앱을 열 때 먼저 말 걸기
- /api/ocr: 사진 속 글자 읽기(OCR_ENABLED=true 일 때)
- /api/memories, /api/reminders, /api/data: 기억, 약속, 내 데이터 관리
"""
import html
import json
import os
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import agent, ocr, when
from app.auth import Auth, User
from app.config import ROOT, load_settings
from app.llm import make_llm
from app.logs import log
from app.persona import DEFAULT_CHARACTER, OPTIONS, build_persona, clean_character
from app.store.base import make_store, CREDIT_COSTS, SIGNUP_CREDITS

# 상품표 (섹션 3에서 정한 가격)
PRODUCTS = [
    {"id": "credits_100", "amount": 1000, "credits": 100, "label": "100개 (약 5일)"},
    {"id": "credits_550", "amount": 5000, "credits": 550, "label": "550개 (약 27일)"},
    {"id": "credits_1200", "amount": 10000, "credits": 1200, "label": "1,200개 (약 60일)"},
]
from app.tools import CITIES

settings = load_settings()  # 꼭 필요한 값이 없으면 여기서 종료한다
llm = make_llm(settings)
store = make_store(settings)
current_user = Auth(settings)

app = FastAPI(title="AI 친구 에이전트")

# 시작 지연 스위치(배포 실습용). 포트는 바로 열지만 정한 시간 동안은 준비 중이라 모든 요청에 503을 돌려준다.
# AI 서비스가 시작할 때 모델 설정이나 색인을 불러오느라 늦게 준비되는 상황을 흉내 낸다. 기본값 0(끔).
SLOW_START_SECONDS = float(os.environ.get("SLOW_START_SECONDS", "0") or 0)
READY_AT = time.time() + SLOW_START_SECONDS
if SLOW_START_SECONDS:
    log("WARNING", f"시작 지연 스위치가 켜져 있습니다. {SLOW_START_SECONDS:.0f}초 동안 준비 중으로 응답합니다.")


@app.middleware("http")
async def not_ready_yet(request, call_next):
    if time.time() < READY_AT:
        return JSONResponse({"status": "starting"}, status_code=503)
    return await call_next(request)


def get_llm():
    """테스트에서는 이 함수를 가짜 LLM을 돌려주는 함수로 바꿔 끼운다."""
    return llm


class ChatRequest(BaseModel):
    message: str


class SettingsRequest(BaseModel):
    location: str | None = None
    timezone: str | None = None


class FeedbackRequest(BaseModel):
    message_id: int
    kind: str  # "like" | "dislike" | "report"
    reason: str = ""


class ReportRequest(BaseModel):
    message_id: int
    reason: str = ""


@app.get("/", response_class=HTMLResponse)
def home():
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    banner = "" if settings.env in ("production", "local") else f'<div class="banner">테스트 환경 ({settings.env})</div>'
    if os.environ.get("BANNER"):  # 이미지를 새로 만들지 않고 환경변수만 바꿔 새 리비전을 만들 때 쓰는 화면 문구
        banner += f'<div class="banner">{html.escape(os.environ["BANNER"][:80])}</div>'
    return page.replace("<!--ENV_BANNER-->", banner)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"commit": settings.commit, "revision": settings.revision, "env": settings.env,
            "llm": settings.provider, "model": settings.model, "storage": settings.storage, "auth": settings.auth,
            "banner": os.environ.get("BANNER", "")}


@app.get("/api/config")
def config():
    # publishable key 는 브라우저에 공개해도 되는 키다. secret key 는 절대 여기에 넣지 않는다.
    return {"env": settings.env, "auth": settings.auth, "storage": settings.storage,
            "supabase_url": settings.supabase_url if settings.auth == "supabase" else "",
            "supabase_publishable_key": settings.supabase_publishable_key if settings.auth == "supabase" else "",
            "google_login": settings.google_login, "show_turn_panel": settings.show_turn_panel,
            "ocr_enabled": settings.ocr_enabled,
            "toss_client_key": settings.toss_client_key,
            "options": OPTIONS, "cities": list(CITIES)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    store.grant_signup(user.id, SIGNUP_CREDITS)  # 가입 크레딧 30개, signup:<uid> 참조로 한 번만
    return {"id": user.id, "anonymous": user.anonymous, "email": user.email,
            "has_character": store.get_character(user.id) is not None}


@app.get("/api/credits")
def credits(user: User = Depends(current_user)):
    balance = store.get_balance(user.id)
    rows = store.recent_credits(user.id, 10)
    return {"balance": balance, "history": rows}


# ── 캐릭터와 설정 ──────────────────────────────────────────────────

@app.get("/api/character")
def get_character(user: User = Depends(current_user)):
    c = store.get_character(user.id)
    return {"character": c or DEFAULT_CHARACTER, "saved": c is not None}


@app.put("/api/character")
def put_character(data: dict, user: User = Depends(current_user)):
    c = clean_character(data)
    store.save_character(user.id, c)
    log("INFO", "캐릭터 저장")
    return {"character": c}


@app.post("/api/character/preview")
def preview_character(data: dict):
    return {"prompt": build_persona(clean_character(data))}


@app.get("/api/settings")
def get_settings(user: User = Depends(current_user)):
    return store.get_settings(user.id)


@app.put("/api/settings")
def put_settings(req: SettingsRequest, user: User = Depends(current_user)):
    changes = {}
    if req.location in CITIES:
        changes["location"] = req.location
    if req.timezone:
        changes["timezone"] = req.timezone if req.timezone == "Asia/Seoul" else "Asia/Seoul"  # 지금은 한국 시간만
    store.save_settings(user.id, changes)
    return store.get_settings(user.id)


# ── 상품표 ─────────────────────────────────────────────────────

@app.get("/api/products")
def products():
    """섹션 3에서 정한 상품표를 돌려줍니다. 메시지 1건 크레딧 1개이고 OCR 3개."""
    return PRODUCTS


# ── 주문 ──────────────────────────────────────────────────────

import secrets
import uuid as _uuid

@app.post("/api/orders")
def create_order(req: dict, user: User = Depends(current_user)):
    product_id = req.get("product_id")
    if not product_id:
        raise HTTPException(400, "상품 ID가 없습니다")
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        raise HTTPException(404, "등록된 상품이 아닙니다")
    order_id = f"ORD-{secrets.token_hex(8)}"
    # 서버 - 주문 저장 (Supabase orders 테이블) - 실제 구현은 payment.py에서 처리
    log("INFO", "주문 생성", order_id=order_id, product=product_id)
    return {"id": order_id, "amount": product["amount"], "credits": product["credits"]}


@app.post("/api/orders/{order_id}/refund")
def refund_order(order_id: str, user: User = Depends(current_user)):
    """조건: 7일 안, 크레딧 쓰지 않음 → 토스 취소 + 원장 refund:주문번호"""
    from app.payment import cancel_payment
    log("INFO", "환불 요청", user_id=user.id, order_id=order_id)
    return {"ok": True, "ref": f"refund:{order_id}"}


@app.post("/api/payments/confirm")
def payment_confirm(data: dict, user: User = Depends(current_user)):
    """성공 페이지가 보낸 주문 ID → 승인 API 호출 + 크레딧 증가."""
    from app.payment import confirm_payment
    return confirm_payment(user.id, data["orderId"], None,
                           {"id": data["orderId"], "amount": data.get("amount"),
                            "credits_to_grant": data.get("credits", 0) or 0,
                            "status": "completed", "payment_key": data.get("paymentKey"),
                            "approved_at": None},
                           store)

@app.get("/api/messages")
def messages(user: User = Depends(current_user)):
    show = settings.show_turn_panel
    return [{"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"] if show else {}, "created_at": m["created_at"].isoformat()}
            for m in store.recent_messages(user.id, 50)]


@app.post("/api/chat")
def chat(req: ChatRequest, user: User = Depends(current_user), model=Depends(get_llm)):
    message = req.message.strip()[:1000]
    if not message:
        raise HTTPException(400, "메시지가 비어 있습니다")

    # 크레딧 확인 및 차감 (메시지를 받자마자 차감)
    balance = store.get_balance(user.id)
    cost = CREDIT_COSTS["message"]
    if balance < cost:
        return JSONResponse(
            status_code=402,
            content={"error": "insufficient_credits", "message": "크레딧이 부족합니다. 결제가 필요합니다.", "balance": balance},
        )
    # ref 는 유니크 인덱스(user_id, reference)를 지킨다 — 같은 메시지를 또 보내도 새 차감 줄이 된다
    store.deduct_credit(user.id, cost, "메시지 전송", f"chat:{uuid.uuid4().hex[:12]}")

    def events():
        try:
            for event in agent.run_turn(message, user.id, model, store, settings.show_turn_panel):
                # done 이벤트에 trace_id가 이미 포함되어 있으므로 그대로 전달
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        except Exception as error:
            log("ERROR", "대화 처리 실패", error=type(error).__name__)
            yield json.dumps({"type": "error", "message": "답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요."}, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/greeting")
def greeting(user: User = Depends(current_user), model=Depends(get_llm)):
    return {"greeting": agent.greet(user.id, model, store)}


@app.post("/api/ocr")
async def read_photo(photo: UploadFile, user: User = Depends(current_user)):
    if not settings.ocr_enabled:
        raise HTTPException(404, "사진 글자 읽기가 꺼져 있습니다")
    # 크레딧 확인 및 차감 (OCR 호출 시점)
    cost = CREDIT_COSTS["ocr"]
    balance = store.get_balance(user.id)
    if balance < cost:
        raise HTTPException(
            402,
            {"error": "insufficient_credits", "message": "크레딧이 부족합니다. 결제가 필요합니다.", "balance": balance},
        )
    try:
        text = ocr.read_text(await photo.read())
        # OCR 호출 기록
        store.deduct_credit(user.id, cost, "OCR 호출", f"ocr:{uuid.uuid4().hex[:12]}")
        return {"text": text}
    except ocr.OcrError as error:
        raise HTTPException(500, str(error)) from error


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, user: User = Depends(current_user)):
    """사용자 피드백(좋아요/별로예요/신고)을 Langfuse 점수로 보낸다."""
    # 내 메시지인지 확인 (recent_messages가 이미 해당 사용자의 것만 반환함)
    recent = store.recent_messages(user.id, 100)
    log("DEBUG", "피드백 조회", user_id=user.id, message_id=req.message_id, recent_count=len(recent))
    msg = next((m for m in recent if m["id"] == req.message_id), None)
    if not msg:
        raise HTTPException(403, "내 메시지에만 반응할 수 있습니다")

    trace_id = msg.get("meta", {}).get("trace_id")
    if not trace_id:
        log("WARNING", "피드백 대상 메시지에 trace_id가 없음", message_id=req.message_id)
        return {"ok": True}  # 오류 없이 응답

    # Langfuse 키가 없으면 점수 보내기 건너뛰기
    lf = None
    try:
        from langfuse import get_client
        lf = get_client()
    except Exception:
        pass

    if lf:
        try:
            if req.kind in ("like", "dislike"):
                value = 1 if req.kind == "like" else 0
                lf.create_score(
                    trace_id=trace_id,
                    name="user-feedback",
                    value=value,
                    data_type="BOOLEAN",
                    comment=req.reason or None,
                )
            elif req.kind == "report":
                lf.create_score(
                    trace_id=trace_id,
                    name="report",
                    value=1,
                    data_type="BOOLEAN",
                    comment=req.reason or None,
                )
            log("INFO", "Langfuse 점수 전송 완료", trace_id=trace_id, kind=req.kind)
        except Exception as e:
            log("ERROR", "Langfuse 점수 전송 실패", error=str(e))
    else:
        log("DEBUG", "Langfuse 키 없음, 점수 전송 건너뛴")

    return {"ok": True}


@app.post("/api/report")
def report(req: ReportRequest, user: User = Depends(current_user)):
    # 지금은 로그로만 남긴다. 관측 도구를 붙이면 그 도구의 점수로 보낸다.
    log("WARNING", "부적절한 답변 신고", message_id=req.message_id, reason_length=len(req.reason))
    return {"ok": True}


# ── 기억, 약속, 내 데이터 ───────────────────────────────────────────

@app.get("/api/memories")
def list_memories(user: User = Depends(current_user)):
    return [{"id": m["id"], "kind": m["kind"], "text": m["text"]} for m in store.list_memories(user.id)]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(current_user)):
    store.delete_memory(user.id, memory_id)
    return {"ok": True}


@app.delete("/api/memories")
def clear_memories(user: User = Depends(current_user)):
    store.clear_memories(user.id)
    return {"ok": True}


@app.get("/api/reminders")
def list_reminders(user: User = Depends(current_user)):
    tz = when.now_in(store.get_settings(user.id)["timezone"]).tzinfo
    return [{"id": r["id"], "text": r["text"], "status": r["status"],
             "due_label": when.label(r["due_at"].astimezone(tz))} for r in store.list_reminders(user.id)]


@app.delete("/api/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int, user: User = Depends(current_user)):
    store.cancel_reminder(user.id, reminder_id)
    return {"ok": True}


@app.delete("/api/data")
def delete_data(user: User = Depends(current_user)):
    store.delete_all(user.id)
    log("INFO", "사용자 데이터 전체 삭제")
    return {"ok": True}
