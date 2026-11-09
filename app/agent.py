"""메시지 하나를 처리하는 흐름(한 턴).

① 안전장치: 위기 표현이면 정해진 안전 응답으로 끝낸다(코드 규칙).
② 도구 선택: 모델이 도구를 0~2개 고른다. 확신이 낮으면 되묻는다(router.py).
③ 도구 실행: 날씨, 미세먼지, 공휴일, 약속(tools.py).
④ 답 생성: 캐릭터 지시문 + 기억 + 도구 결과 + 최근 대화로 답을 스트리밍한다.
⑤ 기억 정리: 답을 다 보낸 뒤 같은 요청 안에서 기억 메모와 대화 요약을 갱신한다(memory.py).
   Cloud Run 기본 설정(요청 기반 과금)에서는 응답이 끝난 뒤 CPU가 거의 주어지지 않아서,
   응답 뒤 백그라운드로 돌리면 기억 정리가 멈추거나 늦어진다. 그래서 응답을 닫기 전에 처리한다.

결과는 이벤트(dict)로 하나씩 내보낸다. show_panel 이 꺼져 있으면(production) 개발용 이벤트는 아예 만들지 않는다.
"""
from datetime import timedelta

from langfuse import get_client, observe, propagate_attributes

from app import memory, when
from app.logs import log
from app.persona import DEFAULT_CHARACTER, build_persona
from app.router import choose
from app.safety import CRISIS_REPLY, is_crisis
from app.tools import TOOLS, Context, run_tool
from app.tracing import turn_attributes, greet_attributes

GREETING_GAP = timedelta(hours=6)  # 마지막 대화 뒤 이만큼 지나면 앱을 열 때 먼저 말을 건다


def system_prompt(character, now, location, memories, summary, tool_lines=(), clarify=False, tools_ok=None):
    parts = [build_persona(character),
             f"[지금] {when.label(now)} ({now.tzinfo}), 사용자 지역: {location}\n[달력]\n{when.calendar_hint(now)}"]
    block = memory.memory_block(memories, summary)
    if block:
        parts.append(block)
    if tool_lines:
        parts.append("[도구 결과]\n" + "\n".join(f"- {line}" for line in tool_lines))
    if tools_ok is False:
        # 도구를 안 골랐거나 도구가 실패한 턴. 답이 날짜·사실을 지어내는 것을 막는다(트레이스에서 확인된 문제).
        parts.append("[근거 없는 사실 금지]\n이번 턴에는 성공한 도구 결과가 없다. 날짜, 공휴일, 날씨, 미세먼지 수치를 답에서 단정하지 않는다. 그런 정보가 꼭 필요하면 지금은 확인할 수 없다고 말하고, 다시 확인하자고 한다.")
    if clarify:
        parts.append("[되묻기]\n사용자가 무엇을 원하는지 확실하지 않다. 짧게 되물어서 확인한다.")
    return "\n\n".join(parts)


@observe(as_type="span", name="run-turn")
def run_turn(message, uid, llm, store, show_panel):
    with propagate_attributes(**turn_attributes(uid, "chat")):
        settings = store.get_settings(uid)
        character = store.get_character(uid) or DEFAULT_CHARACTER
        now = when.now_in(settings["timezone"])
        memories, summary, recent = memory.build_context(store, uid)
        history = [{"role": m["role"], "content": m["content"]} for m in recent]
        store.add_message(uid, "user", message)

        # ① 위기 표현
        if is_crisis(message):
            guards = ["위기 표현이 있어 캐릭터 설정과 상관없이 정해진 안전 응답을 보냈습니다."]
            if show_panel:
                yield {"type": "turn", "tools": [], "confidence": None, "reason": "", "decided_by": "rule",
                       "action": "safety", "guards": guards}
            yield {"type": "delta", "text": CRISIS_REPLY}

            trace_id = None
            try:
                trace_id = get_client().get_current_trace_id()
            except Exception:
                pass

            saved = store.add_message(uid, "assistant", CRISIS_REPLY,
                {"action": "safety", "guards": guards, "trace_id": trace_id})
            log("WARNING", "위기 표현 안전 응답")
            # 안전 응답일 때도 메타데이터 업데이트
            get_client().update_current_span(
                metadata={"commit": turn_attributes(uid, "chat")["version"], "safety": True}
            )
            yield {"type": "done", "message_id": saved["id"], "trace_id": trace_id}
            return

    # ② 도구 선택
    choice = choose(message, history, now, settings["location"], llm, uid)
    if show_panel:
        yield {"type": "turn", "tools": [{"name": t["name"], "label": TOOLS[t["name"]]["label"], "args": t["args"]}
                                         for t in choice.tools],
              "confidence": choice.confidence, "reason": choice.reason, "decided_by": choice.decided_by,
              "action": choice.action, "guards": choice.guards}

    # 도구 선택 후 트레이스 속성 업데이트 (도구, 확신도, 누가 정했는지)
    get_client().update_current_span(
        metadata={
            "commit": turn_attributes(uid, "chat")["version"],
            "tools": [t["name"] for t in choice.tools],
            "confidence": choice.confidence,
            "decided_by": choice.decided_by,
            "safety": choice.action == "safety",
            "tool_names": [t["name"] for t in choice.tools],
        }
    )

    # ③ 도구 실행
    ctx = Context(store, uid, settings["location"], settings["timezone"])
    results = [run_tool(t["name"], t["args"], ctx) for t in choice.tools]
    for r in results:
        if show_panel:
            yield {"type": "tool", "name": r.name, "label": TOOLS[r.name]["label"], "ok": r.ok,
                   "summary": r.summary, "data": r.data}
        if r.ok and r.name in ("create_reminder", "cancel_reminder"):
            yield {"type": "reminder", "action": r.name, "summary": r.summary}

    # ④ 답 생성
    system = system_prompt(character, now, settings["location"], memories, summary,
                           [r.summary for r in results], clarify=choice.action == "clarify",
                           tools_ok=any(r.ok for r in results) if results else False)
    if show_panel:
        yield {"type": "context", "memories": [m["text"] for m in memories],
               "summary": summary["text"] if summary else None, "recent_messages": len(history)}
    reply = ""
    for chunk in llm.chat_stream([{"role": "system", "content": system}, *history, {"role": "user", "content": message}]):
        reply += chunk
        yield {"type": "delta", "text": chunk}

    # 현재 트레이스 ID 가져오기 (Langfuse)
    trace_id = None
    try:
        trace_id = get_client().get_current_trace_id()
    except Exception:
        pass

    meta = {"tools": [t["name"] for t in choice.tools], "confidence": choice.confidence,
            "action": choice.action, "guards": choice.guards, "trace_id": trace_id}
    saved = store.add_message(uid, "assistant", reply, meta)

    # ⑤ 기억 정리
    yield {"type": "memory_start"}
    changes = memory.update_memories(store, uid, message, reply, now, llm)
    memory.maybe_summarize(store, uid, llm)
    yield {"type": "memory", "added": [m["text"] for m in changes["added"]],
           "updated": [m["text"] for m in changes["updated"]], "removed": [m["text"] for m in changes["removed"]]}
    yield {"type": "done", "message_id": saved["id"], "trace_id": trace_id}


@observe(as_type="span", name="greet")
def greet(uid, llm, store):
    """앱을 열 때 먼저 말을 건다. 때가 된 약속이 있거나, 대화가 처음이거나, 오래 쉬었을 때만.

    약속은 '전달됨'으로 바꾸는 데 성공한 것만 전한다. 창을 두 개 열어도 같은 약속을 두 번 전하지 않는다.
    """
    with propagate_attributes(**greet_attributes(uid)):
        settings = store.get_settings(uid)
        character = store.get_character(uid) or DEFAULT_CHARACTER
        now = when.now_in(settings["timezone"])
        due = store.claim_due_reminders(uid, now)
        last = store.recent_messages(uid, 1)
        if not due and last and now - last[-1]["created_at"] < GREETING_GAP:
            return None

        memories, summary, _ = memory.build_context(store, uid)
        lines = [f"약속 알림: '{r['text']}' ({when.label(r['due_at'].astimezone(now.tzinfo))} 예정이었음)" for r in due]
        system = system_prompt(character, now, settings["location"], memories, summary, lines)
        guide = ["사용자가 방금 앱을 열었다. 네가 먼저 1~2문장으로 말을 건다."]
        guide.append("[도구 결과]의 약속 알림을 꼭 전한다." if due else "전할 약속은 없다. 약속이나 알림 이야기를 꺼내지 않는다.")
        guide.append("기억 메모에 오늘이거나 이미 지난 일정이 있으면 어땠는지 물어본다.")
        if not last:
            guide.append("처음 만난 사이다. 이름을 넣어 짧게 자기소개를 한다.")
        system += "\n\n[먼저 말 걸기]\n" + " ".join(guide)
        text = llm.complete_text([{"role": "system", "content": system},
                                  {"role": "user", "content": "(앱을 열었다. 먼저 말을 걸어 줘)"}])

        # 현재 트레이스 ID 가져오기 (Langfuse)
        trace_id = None
        try:
            trace_id = get_client().get_current_trace_id()
        except Exception:
            pass

        saved = store.add_message(uid, "assistant", text,
            {"action": "greeting", "reminders": [r["id"] for r in due], "trace_id": trace_id})
        log("INFO", "먼저 말 걸기", reminders=len(due))
        return {"id": saved["id"], "text": text, "reminders": [r["text"] for r in due], "trace_id": trace_id}
