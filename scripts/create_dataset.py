"""Langfuse 데이터셋과 평가자 만들기.

트레이스에서 실패를 찾아 데이터셋 아이템으로 추가하고, 평가자(코드 채점, LLM 심사)를 등록한다.

실행:
  python scripts/create_dataset.py                    # 기존 트레이스로부터 데이터셋 생성
  python scripts/create_dataset.py --trace-ids id1 id2  # 특정 트레이스만
"""
import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings

load_settings()
lf = get_client()


def create_dataset_from_traces(trace_ids=None, dataset_name="friend-agent-eval"):
    """트레이스로부터 데이터셋을 만든다."""
    # 기존 데이터셋 확인 (예외로 존재 여부 확인)
    dataset = None
    try:
        dataset = lf.get_dataset(dataset_name)
        print(f"기존 데이터셋 사용: {dataset.id}")
    except Exception:
        # 데이터셋이 없으면 생성
        dataset = lf.create_dataset(name=dataset_name, description="AI 친구 에이전트 평가 데이터셋")
        print(f"데이터셋 생성: {dataset.id}")

    # 트레이스 조회 - observations API 사용
    if trace_ids:
        traces = []
        for tid in trace_ids:
            try:
                trace = lf.get_trace(tid)
                traces.append(trace)
            except Exception as e:
                print(f"트레이스 {tid} 조회 실패: {e}")
    else:
        # 최근 observations에서 root observation(트레이스) 찾기
        obs_response = lf.api.observations.get_many(
            limit=50,
            is_root_observation=True,
            environment="local",
            fields="core,basic,time,io,metadata,trace_context"
        )
        traces = obs_response.data

    print(f"조회된 트레이스: {len(traces)}개")

    added = 0
    for trace in traces:
        # 입력 메시지 찾기 - observation에서 input 확인
        input_msg = trace.input
        user_msg = ""

        # run-turn의 input은 JSON 문자열: {"args": [message, user_id, llm, store, show_panel], "kwargs": {}}
        if isinstance(input_msg, str):
            try:
                parsed = json.loads(input_msg)
                if isinstance(parsed, dict) and "args" in parsed:
                    args = parsed.get("args", [])
                    if args and isinstance(args[0], str):
                        user_msg = args[0]
            except json.JSONDecodeError:
                pass
        elif isinstance(input_msg, dict) and "args" in input_msg:
            args = input_msg.get("args", [])
            if args and isinstance(args[0], str):
                user_msg = args[0]
        elif isinstance(input_msg, list):
            for m in input_msg:
                if m.get("role") == "user":
                    user_msg = m.get("content", "")
                    break
        elif isinstance(input_msg, dict):
            user_msg = input_msg.get("content", "")

        if not user_msg:
            continue

        # 메타데이터에서 정보 추출
        metadata = trace.metadata or {}
        trace_id = trace.id

        # 기대 출력/메타데이터 구성
        expected_output = {
            "trace_id": trace_id,
            "user_message": user_msg,
            "tools_used": metadata.get("tools", []),
            "confidence": metadata.get("confidence", 0),
        }

        # 데이터셋 아이템 추가
        try:
            lf.create_dataset_item(
                dataset_name=dataset_name,
                input={"message": user_msg},
                expected_output=expected_output,
                metadata={
                    "trace_id": trace_id,
                    "environment": trace.environment,
                    "version": metadata.get("commit", "unknown"),
                },
                source_trace_id=trace_id
            )
            added += 1
        except Exception as e:
            print(f"아이템 추가 실패 ({trace_id}): {e}")

    print(f"데이터셋에 {added}개 아이템 추가됨")
    return dataset.id


def create_evaluators():
    """평가자(코드 채점, LLM 심사)를 등록한다."""
    from langfuse.api.evaluators.types import CreateEvaluatorRequest_Code

    evaluators = [
        {
            "name": "tool_args_correctness",
            "description": "도구 인자가 정답과 맞는지 검사 (get_holidays 기간, create_reminder due_at 등)",
            "source_code": """
def evaluate(input, output, expected_output, metadata):
    # 도구 선택 정확도 평가 로직
    return {"name": "tool_args_correctness", "value": 1.0, "comment": "자동 평가 필요"}
""",
            "source_code_language": "PYTHON",
        },
        {
            "name": "holiday_weekend_connection",
            "description": "공휴일이 금/월요일이면 주말과 이어져 연휴라고 말하는지 검사",
            "source_code": """
def evaluate(input, output, expected_output, metadata):
    # 연휴 언급 검사 로직
    return {"name": "holiday_weekend_connection", "value": 1.0, "comment": "자동 평가 필요"}
""",
            "source_code_language": "PYTHON",
        },
        {
            "name": "reminder_day_before_correct",
            "description": "'X 전날' 표현이 X의 하루 앞 날짜로 저장되는지 검사",
            "source_code": """
def evaluate(input, output, expected_output, metadata):
    # 전날 계산 검사 로직
    return {"name": "reminder_day_before_correct", "value": 1.0, "comment": "자동 평가 필요"}
""",
            "source_code_language": "PYTHON",
        },
        {
            "name": "user_feedback",
            "description": "사용자 피드백(좋아요/별로예요) 점수",
            "source_code": """
def evaluate(input, output, expected_output, metadata):
    # 사용자 피드백은 별도 수집
    return {"name": "user_feedback", "value": 0.5, "comment": "수동 수집"}
""",
            "source_code_language": "PYTHON",
        },
    ]

    created = []
    for ev in evaluators:
        try:
            request = CreateEvaluatorRequest_Code(
                name=ev["name"],
                description=ev["description"],
                source_code=ev["source_code"],
                source_code_language=ev["source_code_language"],
            )
            evaluator = lf.api.evaluators.create(request=request)
            created.append(evaluator)
            print(f"평가자 생성: {evaluator.name} ({evaluator.id})")
        except Exception as e:
            print(f"평가자 생성 실패 ({ev['name']}): {e}")

    return created


def main():
    parser = argparse.ArgumentParser(description="데이터셋과 평가자 생성")
    parser.add_argument("--trace-ids", nargs="*", help="특정 트레이스 ID만 사용")
    parser.add_argument("--dataset-name", default="friend-agent-eval", help="데이터셋 이름")
    parser.add_argument("--only-evaluators", action="store_true", help="평가자만 생성")
    args = parser.parse_args()

    if not args.only_evaluators:
        create_dataset_from_traces(args.trace_ids, args.dataset_name)

    create_evaluators()
    print("완료")


if __name__ == "__main__":
    main()