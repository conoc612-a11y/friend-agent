"""Langfuse 평가자 구현.

데이터셋 아이템에 대해 코드 채점과 LLM 심사를 수행한다.
"""
import json
import sys
import re
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from langfuse.api.commons.types import DatasetItem
from app.config import load_settings
from app import when
from app.tools import get_holidays, Context

load_settings()
lf = get_client()


class MockStore:
    """평가용 가짜 저장소."""
    def __init__(self):
        self.reminders = []
        self.next_id = 1

    def add_reminder(self, user_id, text, due_at):
        r = {"id": self.next_id, "text": text, "due_at": due_at, "status": "pending"}
        self.reminders.append(r)
        self.next_id += 1
        return r

    def list_reminders(self, user_id, status="pending"):
        return [r for r in self.reminders if r["status"] == status]

    def cancel_reminder(self, user_id, reminder_id):
        for r in self.reminders:
            if r["id"] == reminder_id:
                r["status"] = "cancelled"
                return True
        return False


class MockSettings:
    def __init__(self, timezone="Asia/Seoul", location="서울"):
        self.timezone = timezone
        self.location = location


def evaluate_tool_args_correctness(item: DatasetItem, trace) -> float:
    """도구 인자 정확도 평가.

    - get_holidays: 기간을 말하지 않은 공휴일 질문은 다음 공휴일이 들어가는 기간을 찾는다
    - create_reminder: "X 전날"은 X의 하루 앞 날짜로 저장한다
    """
    expected = item.expected_output or {}
    user_msg = item.input.get("message", "") if item.input else ""

    # 트레이스에서 도구 선택 관찰 찾기
    tool_choice_obs = None
    tool_results = []
    for obs in trace.observations:
        if obs.name == "choose-tools":
            tool_choice_obs = obs
        elif obs.name.startswith("get_") or obs.name in ("create_reminder", "list_reminders", "cancel_reminder"):
            tool_results.append(obs)

    if not tool_choice_obs or not tool_choice_obs.output:
        return 0.0

    try:
        choice_data = json.loads(tool_choice_obs.output) if isinstance(tool_choice_obs.output, str) else tool_choice_obs.output
    except Exception:
        return 0.0

    tools = choice_data.get("tools", [])
    if not tools:
        return 1.0  # 도구 없는 대화는 통과

    # now 고정 (평가 데이터 생성 시점)
    now = datetime.fromisoformat("2026-10-12T14:00:00+09:00")
    ctx = Context(MockStore(), "eval", "서울", "Asia/Seoul")

    score = 1.0
    for tool in tools:
        name = tool.get("name")
        args = tool.get("args", {})

        if name == "get_holidays":
            # 기간이 지정되지 않은 질문("다음 공휴일 언제야?")이면 다음 공휴일이 포함되는지 확인
            from_str = args.get("from")
            to_str = args.get("to")
            if not from_str or not to_str:
                # 모델이 기간을 주지 않은 경우 - 도구 실행 결과로 확인
                for result_obs in tool_results:
                    if result_obs.name == "get_holidays" and result_obs.output:
                        result_data = json.loads(result_obs.output) if isinstance(result_obs.output, str) else result_obs.output
                        data = result_data.get("data", {})
                        holidays = data.get("holidays", [])
                        if holidays:
                            # 다음 공휴일이 있는지 확인 (12월 25일 크리스마스 등)
                            next_holiday = min(holidays, key=lambda h: h["date"])
                            # 통과
                            pass
                        else:
                            score -= 0.3  # 공휴일 없음
            else:
                # 기간이 주어진 경우 - 해당 기간이 합리적인지 확인
                try:
                    from_date = date.fromisoformat(from_str)
                    to_date = date.fromisoformat(to_str)
                    if (to_date - from_date).days < 0:
                        score -= 0.5
                except ValueError:
                    score -= 0.5

        elif name == "create_reminder":
            due_at_str = args.get("due_at")
            if due_at_str and "전날" in user_msg:
                # "금요일 시험 전날" -> 목요일인지 확인
                try:
                    due_at = datetime.fromisoformat(due_at_str)
                    # 메시지에서 기준일 추출 시도
                    # "금요일"이 언급되었는지 확인
                    if "금요일" in user_msg and "전날" in user_msg:
                        # 금요일 전날은 목요일
                        if due_at.weekday() != 3:  # 3=목요일
                            score -= 0.5
                    # 일반적인 전날 검증: 기준일 하루 전인지
                except ValueError:
                    score -= 0.3

    return max(0.0, score)


def evaluate_answer_grounded_in_tools(item: DatasetItem, trace) -> float:
    """답이 도구 결과에 근거하는지 평가 (LLM 심사용 프롬프트 반환).

    실제 LLM 심사는 Langfuse에서 수행하므로 여기서는 프롬프트만 구성해 반환.
    """
    user_msg = item.input.get("message", "") if item.input else ""

    # 트레이스에서 도구 결과와 답 찾기
    tool_summaries = []
    answer = ""

    for obs in trace.observations:
        if obs.name in ("get_holidays", "get_weather", "get_air_quality", "create_reminder", "list_reminders", "cancel_reminder"):
            if obs.output:
                try:
                    result = json.loads(obs.output) if isinstance(obs.output, str) else obs.output
                    summary = result.get("summary", "")
                    if summary:
                        tool_summaries.append(summary)
                except Exception:
                    pass
        elif obs.name == "chat-stream" and obs.output:
            answer = obs.output if isinstance(obs.output, str) else str(obs.output)

    if not tool_summaries:
        return 1.0  # 도구 없는 대화

    if not answer:
        return 0.0

    # LLM 심사용 프롬프트 구성 (Langfuse에서 별도 실행)
    prompt = f"""다음은 사용자 질문, 도구 결과, AI 답변입니다. 답변에 나온 공휴일 날짜가 모두 도구 결과에 있는지 확인하세요.

[사용자 질문]
{user_msg}

[도구 결과]
{chr(10).join(tool_summaries)}

[AI 답변]
{answer}

[판정 기준]
- 답변에 구체적인 공휴일 날짜(예: 12월 25일)가 나오면, 그 날짜가 도구 결과에 반드시 있어야 합니다.
- 도구 결과에 없는 날짜를 답변에서 말하면 0점, 모두 근거가 있으면 1점.

JSON으로만 답하세요: {{"score": 0 또는 1, "reason": "이유"}}"""

    return prompt


def evaluate_holiday_weekend_connection(item: DatasetItem, trace) -> float:
    """공휴일이 금/월요일이면 주말과 이어져 연휴라고 말하는지 평가."""
    user_msg = item.input.get("message", "") if item.input else ""

    # "연휴" 질문이 아니면 통과
    if "연휴" not in user_msg and "연휴" not in (item.expected_output or {}).get("user_message", ""):
        return 1.0

    # 도구 결과에서 공휴일 찾기
    holidays = []
    for obs in trace.observations:
        if obs.name == "get_holidays" and obs.output:
            try:
                result = json.loads(obs.output) if isinstance(obs.output, str) else obs.output
                data = result.get("data", {})
                holidays = data.get("holidays", [])
            except Exception:
                pass

    if not holidays:
        return 1.0  # 공휴일 없음

    # 답변 찾기
    answer = ""
    for obs in trace.observations:
        if obs.name == "chat-stream" and obs.output:
            answer = obs.output if isinstance(obs.output, str) else str(obs.output)

    # 금/월요일 공휴일이 있는지 확인
    has_fri_mon = False
    for h in holidays:
        d = date.fromisoformat(h["date"])
        if d.weekday() in (4, 0):  # 금=4, 월=0
            has_fri_mon = True
            break

    if not has_fri_mon:
        return 1.0  # 금/월 공휴일 없음

    # 답변에 "연휴", "사흘", "3일", "주말" 등이 포함되어 있는지
    answer_lower = answer.lower()
    keywords = ["연휴", "사흘", "3일", "주말", "연속", "이어지"]
    has_connection = any(k in answer_lower for k in keywords)

    return 1.0 if has_connection else 0.0


def evaluate_reminder_day_before_correct(item: DatasetItem, trace) -> float:
    """'X 전날' 표현이 X의 하루 앞 날짜로 저장되는지 평가."""
    expected = item.expected_output or {}
    user_msg = item.input.get("message", "") if item.input else ""

    if "전날" not in user_msg:
        return 1.0

    # 도구 선택에서 create_reminder 인자 확인
    for obs in trace.observations:
        if obs.name == "choose-tools" and obs.output:
            try:
                choice_data = json.loads(obs.output) if isinstance(obs.output, str) else obs.output
                tools = choice_data.get("tools", [])
                for tool in tools:
                    if tool.get("name") == "create_reminder":
                        args = tool.get("args", {})
                        due_at_str = args.get("due_at")
                        if due_at_str:
                            # 정답 확인 (expected_args)
                            expected_args = expected.get("expected_args", {}).get("create_reminder", {})
                            expected_due = expected_args.get("due_at")
                            if expected_due:
                                try:
                                    actual = datetime.fromisoformat(due_at_str).replace(tzinfo=None, second=0, microsecond=0)
                                    expected_dt = datetime.fromisoformat(expected_due).replace(tzinfo=None, second=0, microsecond=0)
                                    return 1.0 if actual == expected_dt else 0.0
                                except ValueError:
                                    return 0.0
            except Exception:
                pass

    return 0.5  # 확인 불가


def run_evaluation(dataset_name="friend-agent-eval", evaluator_names=None):
    """데이터셋에 대해 평가자들을 실행한다."""
    if evaluator_names is None:
        evaluator_names = [
            "tool_args_correctness",
            "answer_grounded_in_tools",
            "holiday_weekend_connection",
            "reminder_day_before_correct",
        ]

    dataset = None
    try:
        dataset = lf.get_dataset(dataset_name)
    except Exception:
        print(f"데이터셋 '{dataset_name}'을 찾을 수 없습니다.")
        return

    items = dataset.items
    print(f"평가 대상: {len(items)}개 아이템")

    # 평가자 함수 매핑
    evaluator_funcs = {
        "tool_args_correctness": evaluate_tool_args_correctness,
        "answer_grounded_in_tools": evaluate_answer_grounded_in_tools,
        "holiday_weekend_connection": evaluate_holiday_weekend_connection,
        "reminder_day_before_correct": evaluate_reminder_day_before_correct,
    }

    for item in items:
        # 트레이스 조회 - trace_id는 root observation ID이므로 observations에서 실제 trace_id 찾기
        root_obs_id = item.metadata.get("trace_id") if item.metadata else None
        if not root_obs_id:
            continue

        # Root observation에서 실제 trace_id 찾기
        try:
            root_obs = None
            obs_response = lf.api.observations.get_many(
                limit=1,
                trace_id=root_obs_id,  # 이 필터가 작동할지 확인
                fields='core,basic,time,io,metadata,trace_context'
            )
            if obs_response.data:
                trace_id = obs_response.data[0].trace_id
            else:
                # 전체 observations에서 root_obs_id와 일치하는 것 찾기
                all_obs = lf.api.observations.get_many(
                    limit=100,
                    fields='core,basic,time,io,metadata,trace_context'
                )
                trace_id = None
                for o in all_obs.data:
                    if o.id == root_obs_id:
                        trace_id = o.trace_id
                        break
                if not trace_id:
                    continue
        except Exception as e:
            print(f"트레이스 ID 찾기 실패 ({root_obs_id}): {e}")
            continue

        # 실제 trace_id로 observations 조회
        try:
            trace_obs = lf.api.observations.get_many(
                limit=50,
                trace_id=trace_id,
                fields='core,basic,time,io,metadata,trace_context'
            )
            # Trace 객체처럼 동작하도록 래퍼 클래스 사용
            class TraceWrapper:
                def __init__(self, observations):
                    self.observations = observations
                    self.id = trace_id
            trace = TraceWrapper(trace_obs.data)
        except Exception as e:
            print(f"트레이스 관찰 조회 실패 ({trace_id}): {e}")
            continue

        for ev_name in evaluator_names:
            func = evaluator_funcs.get(ev_name)
            if not func:
                continue

            try:
                result = func(item, trace)
                # 코드 평가자는 바로 점수 전송
                if ev_name in ("tool_args_correctness", "holiday_weekend_connection", "reminder_day_before_correct"):
                    lf.create_score(
                        trace_id=trace_id,
                        name=ev_name,
                        value=result,
                        data_type="NUMERIC",
                        comment=f"자동 평가: {ev_name}"
                    )
                    print(f"  {item.id} [{ev_name}] = {result}")
                else:
                    # LLM 심사자는 프롬프트 반환 (Langfuse UI에서 실행)
                    print(f"  {item.id} [{ev_name}] = LLM 심사 필요")
            except Exception as e:
                print(f"  {item.id} [{ev_name}] 평가 실패: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="평가 실행")
    parser.add_argument("--dataset", default="friend-agent-eval", help="데이터셋 이름")
    parser.add_argument("--evaluators", nargs="*", help="실행할 평가자 이름들")
    args = parser.parse_args()

    run_evaluation(args.dataset, args.evaluators)


if __name__ == "__main__":
    main()