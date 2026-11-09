"""Langfuse 실험 실행 - 프롬프트 버전 비교.

다른 프롬프트 버전(지시문)으로 같은 데이터셋을 실행하고 결과를 비교한다.

실행:
  python scripts/run_experiment.py --prompt-version v1 --prompt-version v2
  python scripts/run_experiment.py --prompt-name "router-prompt" --versions v1 v2 v3
"""
import json
import sys
import argparse
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from langfuse.api import DatasetItem
from app.config import load_settings
from app.llm import make_llm, FakeLLM
from app.router import choose, router_prompt
from app.tools import Context, TOOLS, run_tool
from app import when
from app.memory import update_memories, maybe_summarize
from app.persona import DEFAULT_CHARACTER, build_persona
from app.safety import is_crisis, CRISIS_REPLY
from app.store.memory_store import MemoryStore

load_settings()
lf = get_client()


class ExperimentRunner:
    def __init__(self, prompt_versions: Dict[str, str]):
        """
        prompt_versions: {버전명: 시스템 프롬프트 문자열}
        예: {"v1": "기존 지시문", "v2": "수정된 지시문"}
        """
        self.prompt_versions = prompt_versions
        self.settings = load_settings()
        self.llm = make_llm(self.settings)
        self.store = MemoryStore()

    def run_single_turn(self, message: str, history: list, version: str, user_id: str = "exp-user") -> Dict:
        """한 턴 실행하고 결과 반환."""
        system_prompt = self.prompt_versions[version]
        now = when.now_in("Asia/Seoul")

        # ① 안전장치
        if is_crisis(message):
            return {"reply": CRISIS_REPLY, "tools": [], "trace_id": None}

        # ② 도구 선택
        choice = choose(message, history, now, "서울", self.llm)

        # ③ 도구 실행
        ctx = Context(self.store, user_id, "서울", "Asia/Seoul")
        results = [run_tool(t["name"], t["args"], ctx) for t in choice.tools]

        # ④ 답 생성
        memories, summary, recent = [], None, history
        tool_lines = [r.summary for r in results]
        system = system_prompt(character=DEFAULT_CHARACTER, now=now, location="서울",
                               memories=memories, summary=summary, tool_lines=tool_lines,
                               clarify=choice.action == "clarify")

        reply = ""
        for chunk in self.llm.chat_stream([{"role": "system", "content": system}, *recent, {"role": "user", "content": message}]):
            reply += chunk

        return {
            "reply": reply,
            "tools": [{"name": t["name"], "args": t["args"]} for t in choice.tools],
            "tool_results": [{"name": r.name, "summary": r.summary, "data": r.data} for r in results],
            "choice": {"confidence": choice.confidence, "reason": choice.reason, "decided_by": choice.decided_by}
        }


def run_experiment(dataset_name: str, prompt_versions: Dict[str, str], experiment_name: str):
    """실험 실행."""
    # 데이터셋 조회
    dataset = None
    for d in lf.get_datasets().data:
        if d.name == dataset_name:
            dataset = d
            break

    if not dataset:
        print(f"데이터셋 '{dataset_name}'을 찾을 수 없습니다.")
        return

    items = lf.get_dataset_items(dataset.id).data
    print(f"실험 '{experiment_name}' 시작: {len(items)}개 아이템 × {len(prompt_versions)}개 버전")

    runner = ExperimentRunner(prompt_versions)

    # 실험 생성
    experiment = lf.create_experiment(
        name=experiment_name,
        dataset_name=dataset_name,
        description=f"프롬프트 버전 비교: {', '.join(prompt_versions.keys())}"
    )

    for version_name, prompt in prompt_versions.items():
        print(f"\n=== 버전: {version_name} ===")

        for i, item in enumerate(items):
            user_msg = item.input.get("message", "") if item.input else ""
            history = item.input.get("history", []) if item.input else []
            expected = item.expected_output or {}

            print(f"  [{i+1}/{len(items)}] {user_msg[:50]}...", end=" ")

            try:
                result = runner.run_single_turn(user_msg, history, version_name)

                # Langfuse에 실험 결과 기록
                trace_id = None
                # 실제로는 run_turn 내부의 트레이스를 사용해야 하지만,
                # 여기서는 실험 결과로 점수만 기록

                # 도구 선택 정확도 평가
                expected_tools = set(expected.get("expected_tools", []))
                actual_tools = set(t["name"] for t in result["tools"])
                tool_correct = expected_tools <= actual_tools

                lf.create_score(
                    trace_id=trace_id or "experiment",
                    name="tool_selection_accuracy",
                    value=1.0 if tool_correct else 0.0,
                    data_type="NUMERIC",
                    comment=f"실험: {experiment_name}, 버전: {version_name}",
                    dataset_item_id=item.id
                )

                # 인자 정확도 평가 (간단 버전)
                args_correct = 1.0
                if tool_correct and expected.get("expected_args"):
                    for tool_name, expected_args in expected["expected_args"].items():
                        actual_tool = next((t for t in result["tools"] if t["name"] == tool_name), None)
                        if actual_tool:
                            # 간단한 비교
                            for key, val in expected_args.items():
                                actual_val = actual_tool["args"].get(key)
                                if key == "due_at":
                                    try:
                                        from datetime import datetime
                                        same = datetime.fromisoformat(str(actual_val)).replace(tzinfo=None, second=0) == datetime.fromisoformat(val)
                                    except ValueError:
                                        same = False
                                elif key in ("from", "to"):
                                    same = str(actual_val or "")[:7] == val[:7]
                                elif key == "day":
                                    try:
                                        from datetime import datetime
                                        expected_date = when.now_in("Asia/Seoul") + timedelta(days=val)
                                        same = str(actual_val or "")[:10] == expected_date.date().isoformat()
                                    except Exception:
                                        same = False
                                else:
                                    same = val in str(actual_val or "")
                                if not same:
                                    args_correct = 0.0
                                    break

                lf.create_score(
                    trace_id=trace_id or "experiment",
                    name="tool_args_accuracy",
                    value=args_correct,
                    data_type="NUMERIC",
                    comment=f"실험: {experiment_name}, 버전: {version_name}",
                    dataset_item_id=item.id
                )

                print(f"도구={'✓' if tool_correct else '✗'} 인자={'✓' if args_correct else '✗'}")

            except Exception as e:
                print(f"실패: {e}")

    print(f"\n실험 완료: {experiment.id}")
    print(f"Langfuse에서 비교 보기: Experiments > {experiment_name}")


def get_prompt_versions():
    """비교할 프롬프트 버전들을 반환."""
    # 기존 router_prompt
    base_prompt = router_prompt()

    # 버전 1: 원본
    v1 = base_prompt

    # 버전 2: "전날은 하루 앞이다" 규칙 추가
    v2 = base_prompt + """

- 'X 전날' 표현(예: '금요일 시험 전날', '크리스마스 전날')은 X의 하루 앞 날짜로 계산한다.
  예: 금요일 전날=목요일, 크리스마스(12/25) 전날=12/24."""

    # 버전 3: 기간 명시 규칙 추가
    v3 = base_prompt + """

- '다음 공휴일 언제야?', '올해 남은 빨간 날' 같이 기간을 명시하지 않은 공휴일 질문은
  오늘부터 앞으로 1년 내의 공휴일을 찾도록 from/to를 비워 둔다.
- 'X월 연휴 있어?'처럼 달만 지정된 질문은 그 달의 1일부터 말일까지로 기간을 잡는다."""

    # 버전 4: 둘 다 적용
    v4 = v2 + """

- '다음 공휴일 언제야?', '올해 남은 빨간 날' 같이 기간을 명시하지 않은 공휴일 질문은
  오늘부터 앞으로 1년 내의 공휴일을 찾도록 from/to를 비워 둔다.
- 'X월 연휴 있어?'처럼 달만 지정된 질문은 그 달의 1일부터 말일까지로 기간을 잡는다."""

    return {
        "v1_original": v1,
        "v2_day_before_rule": v2,
        "v3_date_range_rule": v3,
        "v4_both_rules": v4,
    }


def main():
    parser = argparse.ArgumentParser(description="프롬프트 버전 비교 실험")
    parser.add_argument("--dataset", default="friend-agent-eval", help="데이터셋 이름")
    parser.add_argument("--experiment-name", default="prompt-comparison", help="실험 이름")
    parser.add_argument("--versions", nargs="*", help="실행할 버전 이름들 (기본: 모두)")
    args = parser.parse_args()

    prompt_versions = get_prompt_versions()

    if args.versions:
        prompt_versions = {k: v for k, v in prompt_versions.items() if k in args.versions}

    run_experiment(args.dataset, prompt_versions, args.experiment_name)


if __name__ == "__main__":
    main()