"""Langfuse에서 트레이스와 피드백 점수를 가져온다.

서버 로그와 달리 대화 원문은 랭퓨즈에만 남으므로, 분석은 이 스크립트로 조회한다.
키 값을 출력하지 않는다. 대화 원문이 담긴 결과 파일은 저장소에 올리지 않는다.

랭퓨즈 구버전 조회 API(2026-11-16 종료) 대신 v2 관측 API를 쓴다. 관측 행을
traceId 로 묶어 트레이스 하나를 만들고, 뿌리 관측(parent 가 없는 행)을 트레이스
입출력으로 삼는다.

사용: python scripts/fetch_traces.py --days 30 --json .analysis/traces.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_env_file():
    """키 값을 출력하지 않고 프로세스 환경에만 넣는다."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def fetch_observations(lf, days):
    """v2 관측 API를 커서로 끝까지 읽는다."""
    rows, cursor = [], None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    while True:
        page = lf.api.observations.get_many(
            from_start_time=start,
            to_start_time=end,
            fields="core,basic,io,usage,trace_context",
            expand_metadata="true",
            limit=1000,
            cursor=cursor,
        )
        rows.extend(page.data)
        cursor = getattr(page.meta, "cursor", None)
        if not cursor:
            break
    return rows


def fetch_scores(lf, days):
    """점수(피드백·평가자)를 커서로 끝까지 읽는다."""
    rows, cursor = [], None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    while True:
        page = lf.api.scores.get_many(
            from_timestamp=start, to_timestamp=end, fields="details,subject", limit=100, cursor=cursor
        )
        rows.extend(page.data)
        cursor = getattr(page.meta, "cursor", None)
        if not cursor:
            break
    return rows


def obs_row(o):
    return {
        "id": o.id,
        "trace_id": o.trace_id,
        "parent": o.parent_observation_id,
        "type": o.type,
        "name": o.name,
        "start": o.start_time.isoformat(),
        "end": o.end_time.isoformat() if o.end_time else None,
        "level": o.level,
        "status_message": (o.status_message or "")[:200] if getattr(o, "status_message", None) else None,
        "model": getattr(o, "model", None),
        "usage": {
            "input": getattr(o, "input_tokens", None) if hasattr(o, "input_tokens") else None,
            "output": getattr(o, "output_tokens", None) if hasattr(o, "output_tokens") else None,
            "total": getattr(o, "total_tokens", None) if hasattr(o, "total_tokens") else None,
            "cost": getattr(o, "total_cost", None),
        },
        "metadata": getattr(o, "metadata", None),
        "input": getattr(o, "input", None),
        "output": getattr(o, "output", None),
        "trace_name": getattr(o, "trace_name", None),
        "trace_tags": getattr(o, "tags", None),
        "user_id": getattr(o, "user_id", None),
        "session_id": getattr(o, "session_id", None),
        "environment": getattr(o, "environment", None),
        "trace_release": getattr(o, "release", None),
        "trace_version": getattr(o, "trace_version", None),
        "obs_version": getattr(o, "version", None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    load_env_file()
    from langfuse import Langfuse

    lf = Langfuse()

    obs = fetch_observations(lf, args.days)
    scores = fetch_scores(lf, args.days)
    print(f"관측 {len(obs)}개, 점수 {len(scores)}개")

    by_trace = {}
    for o in obs:
        r = obs_row(o)
        t = by_trace.setdefault(
            r["trace_id"],
            {
                "trace_id": r["trace_id"],
                "name": r["trace_name"] or r["name"],
                "start": r["start"],
                "end": r["end"],
                "tags": r["trace_tags"],
                "user_id": r["user_id"],
                "session_id": r["session_id"],
                "environment": r["environment"],
                "release": r["trace_release"],
                "version": r["trace_version"],
                "input": None,
                "output": None,
                "cost": 0.0,
                "root_metadata": None,
                "observations": [],
            },
        )
        t["cost"] += o.total_cost or 0.0
        t["start"] = min(t["start"], r["start"])
        if r["end"]:
            t["end"] = r["end"]
        if r["parent"] is None:
            t["input"] = r["input"]
            t["output"] = r["output"]
            t["root_metadata"] = r["metadata"]
            t["version"] = r["obs_version"] or t["version"]
            if r["trace_tags"]:
                t["tags"] = r["trace_tags"]
        # 버전(COMMIT_SHA)은 run-turn 같은 최상위 span 행에 붙어 온다. 뿌리가 없는 그룹도 잡는다.
        if r["name"] == "run-turn" and r["obs_version"]:
            t["version"] = r["obs_version"]
        t["observations"].append(r)

    score_rows = []
    for s in scores:
        subject = s.subject
        tid = None
        if isinstance(subject, dict):
            tid = subject.get("id")
        elif subject is not None:
            tid = getattr(subject, "id", None)
        score_rows.append({
            "id": s.id,
            "trace_id": tid,
            "name": s.name,
            "value": s.value,
            "data_type": getattr(s, "data_type", None),
            "comment": (s.comment or "")[:200],
            "ts": s.created_at.isoformat() if getattr(s, "created_at", None) else None,
        })

    # 관측을 시작 시간순으로
    for t in by_trace.values():
        t["observations"].sort(key=lambda r: r["start"])
        t["latency_s"] = None
        if t["end"]:
            from datetime import datetime as dt

            t["latency_s"] = round(
                (dt.fromisoformat(t["end"]) - dt.fromisoformat(t["start"])).total_seconds(), 2
            )

    traces = sorted(by_trace.values(), key=lambda t: t["start"])
    print(f"트레이스 {len(traces)}개")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(exist_ok=True)
        out.write_text(
            json.dumps({"traces": traces, "scores": score_rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"저장: {out}")
    else:
        for t in traces[:10]:
            print(t["start"][:19], t["name"], t["tags"], t["latency_s"])


if __name__ == "__main__":
    main()
