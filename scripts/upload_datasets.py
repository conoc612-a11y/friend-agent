"""Langfuse에 데이터셋 업로드.

data/eval/ 의 네 파일을 각각 Langfuse 데이터셋으로 올린다.
- 이미 같은 id의 항목이 있으면 업데이트(덮어쓰기)한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings

load_settings()
lf = get_client()


def load_jsonl(path):
    """JSONL 파일 로드."""
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def upload_dataset(dataset_name, items, description=""):
    """데이터셋 생성/조회 후 아이템 업로드/업데이트."""
    # 데이터셋 조회 또는 생성
    try:
        dataset = lf.get_dataset(dataset_name)
        print(f"기존 데이터셋 사용: {dataset_name} ({dataset.id})")
    except Exception:
        dataset = lf.create_dataset(name=dataset_name, description=description)
        print(f"데이터셋 생성: {dataset_name} ({dataset.id})")

    uploaded = 0
    updated = 0

    for item in items:
        item_id = item.get("id")
        input_data = {k: v for k, v in item.items() if k not in ("id", "expected_output", "metadata")}
        expected_output = item.get("expected_output", {})
        metadata = item.get("metadata", {})

        try:
            # 기존 아이템 조회 시도
            existing_items = lf.api.dataset_items.list(
                dataset_name=dataset_name,
                limit=100
            )
            existing = None
            for ei in existing_items.data:
                if ei.id == item_id:
                    existing = ei
                    break

            if existing:
                # 업데이트
                lf.api.dataset_items.update(
                    dataset_name=dataset_name,
                    id=item_id,
                    input=input_data,
                    expected_output=expected_output,
                    metadata=metadata
                )
                updated += 1
            else:
                # 새로 생성
                lf.create_dataset_item(
                    dataset_name=dataset_name,
                    input=input_data,
                    expected_output=expected_output,
                    metadata=metadata,
                    id=item_id
                )
                uploaded += 1

        except Exception as e:
            print(f"  아이템 {item_id} 업로드 실패: {e}")

    print(f"  {dataset_name}: 새로 추가 {uploaded}개, 업데이트 {updated}개")
    return uploaded + updated


def main():
    print("=== Langfuse 데이터셋 업로드 ===\n")

    total = 0

    # 1. tool_selection
    items = load_jsonl(ROOT / "data/eval/tool_selection.jsonl")
    # T26에 next_holiday 추가
    for item in items:
        if item.get("id") == "T26":
            item["expected_output"] = item.get("expected_output", {})
            item["expected_output"]["next_holiday"] = "2026-12-25"
    total += upload_dataset("tool-selection", items, "도구 선택 평가 데이터셋")

    # 2. memory_extraction
    items = load_jsonl(ROOT / "data/eval/memory_extraction.jsonl")
    total += upload_dataset("memory-extraction", items, "기억 정리 평가 데이터셋")

    # 3. safety
    items = load_jsonl(ROOT / "data/eval/safety.jsonl")
    total += upload_dataset("safety", items, "안전장치 평가 데이터셋")

    # 4. holiday_reply
    items = load_jsonl(ROOT / "data/eval/holiday_reply.jsonl")
    total += upload_dataset("holiday-reply", items, "공휴일 답 품질 평가 데이터셋")

    print(f"\n총 {total}개 아이템 처리 완료")

    # 확인 출력
    print("\n=== 데이터셋별 항목 수 ===")
    for name in ["tool-selection", "memory-extraction", "safety", "holiday-reply"]:
        try:
            ds = lf.get_dataset(name)
            print(f"  {name}: {len(ds.items)}개")
        except Exception as e:
            print(f"  {name}: 조회 실패 - {e}")


if __name__ == "__main__":
    main()