"""평가 데이터셋을 배포된 서비스에 보내고 코드 평가자로 채점한다.

데이터셋 항목(data/eval/improvement.jsonl) 형식:
  {"id": "P1", "kind": "problem"|"normal", "turns": ["메시지", ...],
   "expect": {"tool": "get_holidays",          # 마지막 턴에서 이 도구가 불려야 한다(생략 가능)
               "no_tool": true,                 # 도구를 쓰면 안 된다
               "ban_ungrounded_dates": true,    # 도구 없이(또는 실패 뒤) 날짜를 말하면 실패
               "reply_contains": ["사흘"],       # 답에 반드시 들어갈 말
               "max_len": 400}}                 # 답 최대 길이

사용: python scripts/run_dataset.py --base https://... --data data/eval/improvement.jsonl --out .analysis/exp_a.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.simulate_users import anon_login, read_turn  # noqa: E402

DATE_TOKENS = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}월\s*\d{1,2}일)")


def evaluate(item, last_turn_result, history_results):
    """expect 를 코드로 검사해 {pass, reasons} 를 돌려준다."""
    expect = item.get("expect") or {}
    reply = last_turn_result["reply"] or ""
    turn = last_turn_result["turn"] or {}
    tools = last_turn_result["tools"] or []
    tool_names = [t["name"] for t in tools]
    ok_flags, fails = [], []

    if expect.get("tool"):
        (ok_flags if expect["tool"] in tool_names else fails).append(
            f"도구 {expect['tool']} 필요 (실제: {tool_names})")
    if expect.get("no_tool") and tool_names:
        fails.append(f"도구를 쓰면 안 됨 (실제: {tool_names})")

    # 도구 없이(또는 도구가 전부 실패했을 때) 날짜를 단정하면 실패
    if expect.get("ban_ungrounded_dates"):
        tool_ok = any(t["ok"] for t in tools)
        dates = set(DATE_TOKENS.findall(reply))
        if dates and not tool_ok:
            fails.append(f"도구 없이 날짜 단정: {sorted(dates)[:3]}")
        elif dates and tool_ok:
            ok_flags.append(f"도구 근거 날짜 {sorted(dates)[:2]}")

    # 도구 없이(또는 실패 뒤) 사실을 단정하면 안 된다: 모른다고 말해야 한다
    if expect.get("unknown_if_no_tool"):
        tool_ok = any(t["ok"] for t in tools)
        if not tool_ok:
            markers = expect["unknown_if_no_tool"]
            if not any(m in reply for m in markers):
                fails.append(f"도구 없이 단정(모름 표현 없음): {reply[:60]!r}")
            else:
                ok_flags.append("모름 표현으로 답함")

    # 도구를 성공했을 때만 할 수 있는 말(예: '취소했') 검사
    claim = expect.get("no_claim_unless_tool")
    if claim and claim["claim"] in reply:
        if claim["tool"] not in tool_names:
            fails.append(f"도구 없이 '{claim['claim']}'라고 주장")
        else:
            ok_flags.append(f"{claim['tool']} 실행 후 말함")

    for sub in expect.get("reply_contains") or []:
        (ok_flags if sub in reply else fails).append(f"답에 '{sub}' 필요")

    limit = expect.get("max_len")
    if limit and len(reply) > limit:
        fails.append(f"답 {len(reply)}자 > {limit}자")

    return {"pass": not fails, "ok": ok_flags, "fail": fails}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=".analysis/exp.json")
    ap.add_argument("--label", default="")
    ap.add_argument("--rounds", type=int, default=1, help="전체 데이터셋을 몇 번 반복할지")
    args = ap.parse_args()

    items = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    http = httpx.Client(timeout=180)
    results = []
    for round_no in range(1, args.rounds + 1):
        # 라운드당 사용자 1명: 가입 속도 제한 때문에 항목을 한 세션에 이어 보낸다(항목당 크레딧 1개)
        token, uid = anon_login(args.base, http)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        http.get(f"{args.base}/api/me", headers=headers)
        http.put(f"{args.base}/api/character", headers=headers,
                 json={"name": "미소", "speech": "반말", "traits": ["직설적", "꼼꼼함"], "job": "대학원생"})
        for item in items:
            history = []
            last = None
            for msg in item["turns"]:
                last = read_turn(args.base, http, token, msg)
                history.append(last)
                time.sleep(0.8)
            checked = evaluate(item, last, history)
            results.append({"id": item["id"], "kind": item["kind"], "user": uid, "round": round_no,
                            "tool_names": [t["name"] for t in (last["tools"] or [])],
                            "tool_ok": [t["ok"] for t in (last["tools"] or [])],
                            "reply": (last["reply"] or "")[:200],
                            "reply_len": len(last["reply"] or ""), "trace_id": last.get("trace_id"),
                            "seconds": last.get("seconds"), **checked})
            print(f"R{round_no} {item['id']} [{'통과' if checked['pass'] else '실패'}] {checked['fail'] or checked['ok']}")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    p = sum(1 for r in results if r["pass"])
    print(f"\n{args.label} 총 {len(results)}항목 중 통과 {p}, 실패 {len(results) - p} → {args.out}")


if __name__ == "__main__":
    main()
