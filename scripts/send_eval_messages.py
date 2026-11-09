"""평가 데이터 메시지로 트레이스 모으기.

data/eval/tool_selection.jsonl 과 data/eval/safety.jsonl 의 message 를
로컬 서버(http://localhost:8080)의 /api/chat 에 하나씩 보내고, 응답 스트림을 끝까지 읽은 뒤 다음 메시지를 보낸다.

실행:
  python scripts/send_eval_messages.py                    # 기본 5개씩
  python scripts/send_eval_messages.py --limit 20         # 20개씩
  python scripts/send_eval_messages.py --limit 0          # 전체
"""
import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE_URL = "http://localhost:8080"


def send_messages(file_path: Path, limit: int, user_id: str = "eval-user"):
    """JSONL 파일의 메시지를 서버에 보낸다."""
    lines = file_path.read_text(encoding="utf-8").splitlines()
    if limit > 0:
        lines = lines[:limit]

    sent = 0
    failed = 0

    for line in lines:
        row = json.loads(line)
        message = row["message"]

        try:
            with httpx.Client(timeout=60.0) as client:
                with client.stream("POST", f"{BASE_URL}/api/chat", json={
                    "message": message,
                    "user_id": user_id
                }) as response:
                    response.raise_for_status()
                    # 스트림을 끝까지 읽음
                    for _ in response.iter_text():
                        pass
            sent += 1
        except Exception as e:
            failed += 1
            print(f"실패: {message[:50]}... - {e}", file=sys.stderr)

    return sent, failed


def main():
    parser = argparse.ArgumentParser(description="평가 메시지 전송")
    parser.add_argument("--limit", type=int, default=5, help="보낼 개수 (0=전체)")
    parser.add_argument("--user-id", type=str, default="eval-user", help="사용자 ID")
    args = parser.parse_args()

    print(f"서버: {BASE_URL}")
    print(f"보낼 개수: {args.limit if args.limit > 0 else '전체'}")
    print(f"사용자 ID: {args.user_id}")
    print()

    total_sent = 0
    total_failed = 0

    for fname in ("tool_selection.jsonl", "safety.jsonl"):
        path = ROOT / "data/eval" / fname
        if not path.exists():
            print(f"파일 없음: {path}")
            continue

        print(f"=== {fname} ===")
        sent, failed = send_messages(path, args.limit, args.user_id)
        total_sent += sent
        total_failed += failed
        print(f"  보낸: {sent}, 실패: {failed}")
        print()

    print(f"총계: 보낸 {total_sent}, 실패 {total_failed}")


if __name__ == "__main__":
    main()