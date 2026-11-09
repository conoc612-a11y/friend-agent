"""시뮬레이션 대화 기록(.analysis/sim_*)과 랭퓨즈 조회 결과에서 문제 후보를 센다.

사용: python scripts/analyze_traces.py --sim .analysis/sim_before --traces .analysis/traces.json
"""
import argparse
import json
import re
from pathlib import Path

HONORIFIC = re.compile(r"(요\?|요\.|요~|예요|이에요|습니다|해요)")


def load_sim(sim_dir):
    rows = []
    for p in sorted(Path(sim_dir).glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["persona"] = p.stem
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=".analysis/sim_before")
    ap.add_argument("--traces", default="")
    args = ap.parse_args()

    rows = load_sim(args.sim)
    print(f"대화 기록 {len(rows)}턴")

    # 후보별 반복 횟수와 사례(트레이스 앞자리)
    buckets = {}
    for r in rows:
        reason = r.get("dislike_reason")
        if not reason:
            continue
        key = reason.split("(")[0]
        b = buckets.setdefault(key, {"n": 0, "examples": []})
        b["n"] += 1
        if len(b["examples"]) < 3:
            b["examples"].append(
                {"trace_id": (r.get("trace_id") or "")[:10], "persona": r["persona"],
                 "n": r["n"], "user": r["user"][:40], "reply_len": len(r.get("reply") or "")}
            )
    print("\n== 별로예요 근거별 횟수 ==")
    for k, b in sorted(buckets.items(), key=lambda kv: -kv[1]["n"]):
        print(f"{b['n']:3d}  {k}")
        for e in b["examples"]:
            print(f"      trace {e['trace_id']} [{e['persona']} #{e['n']}] {e['user']} → {e['reply_len']}자")

    # 되묻기/도구/지연 통계
    clarify = [r for r in rows if (r.get("turn") or {}).get("action") == "clarify"]
    print(f"\n되묻기 턴: {len(clarify)} / {len(rows)}")
    for r in clarify[:5]:
        print(f"   [{r['persona']} #{r['n']}] {r['user'][:40]} trace {(r.get('trace_id') or '')[:10]}")
    no_tool_but_asked = [r for r in rows if r.get("dislike_reason") == "도구가 필요한 요청인데 도구를 안 씀"]
    lat = [r["seconds"] for r in rows if r.get("seconds")]
    if lat:
        lat.sort()
        print(f"\n턴 지연: p50={lat[len(lat)//2]}s p90={lat[int(len(lat)*0.9)]}s max={lat[-1]}s")

    # 답 길이 분포
    lens = sorted(len(r.get("reply") or "") for r in rows)
    if lens:
        print(f"답 길이: p50={lens[len(lens)//2]}자 p90={lens[int(len(lens)*0.9)]}자 max={lens[-1]}자, 500자 초과 {sum(1 for x in lens if x > 500)}턴")

    if args.traces:
        d = json.load(open(args.traces, encoding="utf-8"))
        traces = d["traces"]
        scores = d["scores"]
        fb = [s for s in scores if s["name"] == "user-feedback"]
        print(f"\n랭퓨즈: 트레이스 {len(traces)}개, user-feedback {len(fb)}개 "
              f"(별로예요 {sum(1 for s in fb if s['value'] in (0, False))}, 좋아요 {sum(1 for s in fb if s['value'] in (1, True))})")


if __name__ == "__main__":
    main()
