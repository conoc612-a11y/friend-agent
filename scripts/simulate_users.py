"""여러 사용자 유형이 배포된 서비스와 실제로 대화하고 피드백을 남긴다.

실제 사용자가 할 법한 대화를 사용자 유형(페르소나)별로 보내 트레이스를 쌓고,
답이 별로인 곳에서 화면과 같은 방식(/api/feedback)으로 별로예요를 누른다.
개인정보를 쓰지 않고, 대화 원문은 저장소에 올리지 않고 .analysis/ 에만 남긴다.

로그인이 필요한 서비스(AUTH=supabase)라면 /api/config 의 공개 키로 익명 로그인한다.

사용:
  python scripts/simulate_users.py --base https://...run.app --personas planner,weather --turns 22
"""
import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 사용자 유형별 대화 스크립트. {today} 는 실행 날짜로 바꿔 넣는다.
PERSONAS = {
    # 약속·일정을 자주 등록/취소하고 날짜 계산을 섞어 묻는 유형
    "planner": {
        "character": {"name": "미소", "speech": "반말", "traits": ["직설적", "꼼꼼함"],
                      "job": "대학원생", "relationship": "친구", "avatar": "🐱",
                      "interests": ["도서관", "커피"], "emoji": "가끔"},
        "turns": [
            "나 목요일 오후에 세미나 있어. 그 전날 밤에 일찍 자라고 알려 줘",
            "아 맞다 금요일 시험 있어. 시험 전날에 같이 복습하자고 약속 저장해 줘",
            "다음 공휴일이 언제야? 연휴면 몇 박 며칠 쉬는 거야?",
            "모레 서울 날씨 어때? 야외 스터디 해도 될까?",
            "지금 등록한 약속 다 보여 줘",
            "복습 약속 취소할게. 시험 기간엔 만나지 말자",
            "그럼 다음 주 월요일 저녁 7시에 같이 저녁 먹자. 약속 저장해 줘",
            "월요일 저녁 약속이 몇 시냐 했지? 까먹을 것 같아",
            "토요일에 미세먼지 심하면 도서관 가야지. 토요일 미세먼지 알려 줘",
            "요즘 잠이 안 와서 힘들다. 방법 없을까",
            "주말에 뭐 하고 싶어? 나는 그냥 집에 있고 싶은데",
            "10월 마지막 주에 공휴일 있어?",
            "내일 아침에 우산 챙기라고 알려 줘. 비 온대",
            "아까 말한 세미나 약속 시간이 언제였지?",
            "다음 주에 발표인데 너무 떨려. 도와줄 수 있어?",
            "발표 연습 일정으로 수요일 밤 9시에 알림 하나 더 저장해 줘",
            "수요일 발표 연습 약속 취소. 그냥 혼자 할래",
            "오늘 데이트 할 장소 같은 거 추천해 줄래? 친구랑 놀러 가려고",
            "설날이나 추석 중에 더 가까운 게 언제야?",
            "요즘 너무 바빠서 여행도 못 가겠다. 다음 공휴일에라도 쉬어야지",
            "내가 아까 뭐 저장하라고 했더라? 기억 안 나",
        ],
    },
    # 날씨·미세먼지로 외출을 판단하는 유형. 도시를 바꿔 가며 묻는다
    "weather": {
        "character": {"name": "하늘", "speech": "존댓말", "traits": ["다정함", "차분함"],
                      "job": "바리스타", "relationship": "친구", "avatar": "🦊",
                      "interests": ["등산", "사진"], "emoji": "가끔"},
        "turns": [
            "오늘 서울 날씨 알려 주세요. 자전거 타러 가도 될까요?",
            "부산은요? 이번 주말에 부산 갈 것 같은데",
            "제주도 날씨도 확인해 주실래요? 다음 주에 여행 가요",
            "오늘 미세먼지 어때요? 마스크 써야 하나요",
            "내일 아침 서울 날씨는요? 출근할 때 걱정이네요",
            "모레 부산 날씨도 알려 주세요",
            "다음 공휴일이 언제인가요? 쉬는 날에 등산 가고 싶어요",
            "그 공휴일에 설악산 단풍 볼 수 있을까요?",
            "요즘 날씨가 이상해서요. 주말에 비 많이 와요?",
            "주말에 등산 가려고 했는데, 비 오면 실내로 바꿔야겠어요",
            "서울 이번 주말 미세먼지도 괜찮나요?",
            "요즘 사진 찍는 게 취미인데 좋은 장소 추천해 주세요",
            "오늘 기분이 좀 가라앉아 있네요. 날씨 탓인가요",
            "대화할 사람이 필요해서 그래요. 요즘 뭐 하면서 지내세요?",
            "제주 여행 일정을 토요일 아침 8시에 출발로 약속 저장해 주세요",
            "제주 여행 약속 취소할게요. 항공권이 너무 비싸요",
            "그럼 대신 토요일에 근처 공원 산책이나 하자. 저녁 6시로 저장해 주세요",
            "공원 산책 약속 날짜가 언제라고 하셨죠?",
            "다음 달에 공휴일 있나요? 또 여행 계획 세우고 싶어서요",
            "비 오는 날엔 뭐 하면서 보내세요?",
            "오늘 대화하기 좋네요. 내일 날씨만 다시 알려 주세요",
            "산책 약속 즐거웠어요. 다음에 또 같이 가요",
        ],
    },
    # 일상·감정 위주로 이야기하다가 기억을 확인하고, 정정도 하는 유형
    "friend": {
        "character": {"name": "두리", "speech": "반말", "traits": ["장난스러움", "엉뚱함"],
                      "job": "요리사", "relationship": "친구", "avatar": "🐻",
                      "interests": ["요리", "게임"], "emoji": "자주"},
        "turns": [
            "나 오늘 라면 뱃속에 계란 넣어서 먹었어. 완전 맛있었어",
            "너는 계란 좋아해? 나는 반숙이 최고라 생각해",
            "요즘 야근이 많아서 지치네. 뭐 별거 아니지만",
            "아니 그게 아니라, 야근보다는 요즘 게임할 시간이 없어서 그래",
            "주말에 새 게임 샀어. 턴제 전략 게임인데 재밌어",
            "다음 공휴일 언제야? 그때 게임 정령하고 싶어",
            "토요일 오후 3시에 게임하자고 약속 저장해 줘",
            "게임 약속 날짜가 언제였지? 헷갈린다",
            "토요일 게임 약속 일요일로 바꿔 줘. 일요일 오후 3시",
            "일요일 약속 시간이 몇 시였지?",
            "요즘 요리 실력이 늘었어. 파스타 소스를 직접 만들었거든",
            "내가 어떤 요리 좋아한다고 했지? 기억나?",
            "일요일 게임 약속 취소. 친구가 갑자기 일정이 생겼어",
            "심심해. 재밌는 얘기 하나 해 줘",
            "너무 길어. 한 문장으로만 말해 봐",
            "오늘 따라 기분이 안 좋아. 이유는 잘 모르겠고",
            "고마워, 조금 나은 것 같아. 그런데 나 내일 일정 있었나?",
            "다음 주 화요일 밤 8시에 너랑 보드게임 카페 가자. 저장해 줘",
            "보드게임 카페 약속이 화요일 맞지?",
            "화요일 약속 취소할게. 그냥 집에서 쉴래",
            "요즘은 약속 잡지 말자. 일이 너무 많아",
            "대화해 줘서 고마웠어. 또 얘기하자",
        ],
    },
}


def anon_login(base, http):
    """/api/config 의 공개 정보로 익명 로그인해 (token, user_id) 를 돌려준다."""
    cfg = http.get(f"{base}/api/config").json()
    url, key = cfg["supabase_url"], cfg["supabase_publishable_key"]
    r = http.post(f"{url}/auth/v1/signup", headers={"apikey": key, "Authorization": f"Bearer {key}"},
                  json={}, timeout=20)
    r.raise_for_status()
    data = r.json()
    token, uid = data["access_token"], data["user"]["id"]
    return token, uid


def api(base, http, token, path, method="GET", body=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = http.request(method, f"{base}{path}", headers=headers, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} -> {r.status_code} {r.text[:200]}")
    return r


def read_turn(base, http, token, message):
    """/api/chat 의 ndjson 이벤트를 끝까지 읽고 턴 요약을 돌려준다."""
    t0 = time.time()
    with http.stream("POST", f"{base}/api/chat", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"message": message}, timeout=180) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"/api/chat -> {r.status_code} {r.read()[:200]}")
        reply, turn, tools, memory = "", {}, [], []
        message_id = trace_id = None
        for line in r.iter_lines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["type"] == "turn":
                turn = {k: e.get(k) for k in ("tools", "confidence", "decided_by", "action", "guards")}
            elif e["type"] == "tool":
                tools.append({"name": e["name"], "ok": e["ok"], "summary": e["summary"]})
            elif e["type"] == "delta":
                reply += e["text"]
            elif e["type"] == "memory":
                memory = e
            elif e["type"] == "done":
                message_id, trace_id = e["message_id"], e.get("trace_id")
            elif e["type"] == "error":
                reply += f"[오류] {e.get('message')}"
    return {"reply": reply, "turn": turn, "tools": tools, "memory": memory,
            "message_id": message_id, "trace_id": trace_id, "seconds": round(time.time() - t0, 1)}


HONORIFIC = re.compile(r"(요\?|요\.|요~|예요|이에요|습니다|해요)")


def judge(persona_speech, message, result):
    """별로예요를 누를 근거를 찾는다. 사람이 나중에 검토하므로 이유를 남긴다."""
    reply, turn = result["reply"], result["turn"]
    if result["reply"].startswith("[오류]") or not reply.strip():
        return "빈 답 또는 오류"
    if len(reply) > 500:
        return f"답이 너무 김({len(reply)}자, 메신저 대화 기준 초과)"
    if persona_speech == "반말" and len(HONORIFIC.findall(reply)) >= 3:
        return "캐릭터 설정(반말)과 다른 말투"
    asked_tool = any(k in message for k in ("날씨", "미세먼지", "공휴일", "언제야", "저장해", "취소"))
    used_tool = bool(result["tools"])
    clarify = turn.get("action") == "clarify"
    if asked_tool and not used_tool and not clarify:
        return "도구가 필요한 요청인데 도구를 안 씀"
    if clarify and any(k in message for k in ("날씨", "공휴일", "저장해", "취소", "언제야")):
        return "분명한 요청인데 되묻기"
    if result["tools"] and any(not t["ok"] for t in result["tools"]):
        return "도구 실행 실패"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--personas", default="planner,weather,friend")
    parser.add_argument("--turns", type=int, default=22, help="페르소나당 보낼 턴 수(0=전부)")
    parser.add_argument("--out", default=".analysis/sim")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    http = httpx.Client()
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).strftime("%Y-%m-%d")

    for name in args.personas.split(","):
        spec = PERSONAS[name.strip()]
        turns = spec["turns"] if args.turns in (0, None) else spec["turns"][: args.turns]
        token, uid = anon_login(args.base, http)
        api(args.base, http, token, "/api/character", "PUT", spec["character"])
        api(args.base, http, token, "/api/greeting", "POST")
        print(f"[{name}] 사용자 {uid[:8]}… 대화 {len(turns)}턴 시작")

        rows = []
        for i, message in enumerate(turns, 1):
            msg = message.replace("{today}", today)
            try:
                result = read_turn(args.base, http, token, msg)
            except Exception as error:
                result = {"reply": f"[전송 실패] {error}", "turn": {}, "tools": [],
                          "memory": {}, "message_id": None, "trace_id": None, "seconds": None}
            reason = judge(spec["character"]["speech"], msg, result)
            if reason and result["message_id"]:
                try:
                    api(args.base, http, token, "/api/feedback", "POST",
                        {"message_id": result["message_id"], "kind": "dislike", "reason": reason})
                except Exception as error:
                    print(f"    피드백 실패: {error}")
            rows.append({"n": i, "user": msg, **result, "dislike_reason": reason})
            mark = " ✗ " + reason if reason else ""
            print(f"  {i:02d} {result['seconds']}s {result['turn'].get('action')} "
                  f"tools={[t['name'] for t in result['tools']]} conf={result['turn'].get('confidence')}{mark}")
            time.sleep(1.0)

        path = out_dir / f"{name}.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        bad = sum(1 for r in rows if r["dislike_reason"])
        print(f"[{name}] 완료: {len(rows)}턴, 별로예요 {bad}회 → {path}")


if __name__ == "__main__":
    main()
