"""Langfuse에 프롬프트와 평가자 생성/관리.

- 답 생성 프롬프트 (holiday-reply-answer) 버전 관리
- LLM-as-a-Judge 평가자 (holiday-reply-judge) 생성
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings
from app.persona import build_persona, DEFAULT_CHARACTER, FIXED_RULES
from app import when

load_settings()
lf = get_client()


def create_answer_prompt():
    """holiday-reply-answer 프롬프트 생성 (버전 1: 원본, 버전 2: 수정된 버전)."""
    # 고정 시각: 2026-11-02(월) 14:00 KST
    fixed_now = when.now_in("Asia/Seoul").replace(year=2026, month=11, day=2, hour=14, minute=0, second=0, microsecond=0)
    calendar = when.calendar_hint(fixed_now)
    persona = build_persona(DEFAULT_CHARACTER)

    # Chat 형식: 단일 문자열로 system + user 결합 ({{variable}} 플레이스홀더 포함)
    prompt_template_v1 = f"""{FIXED_RULES}

[캐릭터]
너는 아래 캐릭터로 사용자와 대화한다.
{persona}

[지금] 11월 2일(월) 14:00 (Asia/Seoul), 사용자 지역: 서울
[달력]
{calendar}

[도구 결과]
- {{tool_result}}

{{message}}"""

    try:
        prompt_v1 = lf.create_prompt(
            name="holiday-reply-answer",
            prompt=prompt_template_v1,
            labels=["experiment"],
            tags=["friend-agent", "answer-generation"],
            config={"type": "chat", "fixed_now": "2026-11-02T14:00:00+09:00", "variables": ["tool_result", "message"]}
        )
        print(f"프롬프트 생성: holiday-reply-answer v{prompt_v1.version}")
        print(f"  변수: {prompt_v1.variables}")
    except Exception as e:
        print(f"프롬프트 생성 실패 (v1): {e}")

    # 버전 2: "전날은 하루 앞" 규칙 추가
    prompt_template_v2 = prompt_template_v1 + """

[답 생성 추가 규칙]
- 'X 전날' 표현(예: '금요일 시험 전날', '크리스마스 전날')은 X의 하루 앞 날짜로 계산한다.
  예: 금요일 전날=목요일, 크리스마스(12/25) 전날=12/24.
- 공휴일이 금요일이면 토요일, 일요일과 이어 사흘을 쉰다고 말한다.
- 도구 결과에 없는 날짜를 공휴일이나 연휴라고 말하지 않는다."""

    try:
        prompt_v2 = lf.create_prompt(
            name="holiday-reply-answer",
            prompt=prompt_template_v2,
            labels=["experiment"],
            tags=["friend-agent", "answer-generation", "fixed-rules"],
            config={"type": "chat", "fixed_now": "2026-11-02T14:00:00+09:00", "version": "v2-fixed-rules", "variables": ["tool_result", "message"]}
        )
        print(f"프롬프트 생성: holiday-reply-answer v{prompt_v2.version}")
        print(f"  변수: {prompt_v2.variables}")
    except Exception as e:
        print(f"프롬프트 생성 실패 (v2): {e}")


def create_judge_evaluator():
    """LLM-as-a-Judge 평가자 생성."""
    judge_prompt_system = """너는 AI 친구 서비스가 공휴일 질문에 한 답을 판정하는 평가자다.
서비스는 공휴일 도구의 결과를 받아 답을 만든다. 도구 결과가 사실이고, 답은 도구 결과와 맞아야 한다.
판정할 것은 이 항목의 통과 조건 하나다. 말투, 길이, 이모지, 친절함은 판정하지 않는다.

판정 기준
1. 답이 도구 결과에 없는 날짜를 공휴일이나 쉬는 날이라고 말하면 실패다. 실제로 공휴일인 날이라도 도구 결과에 없으면 실패다.
2. 공휴일이 금요일이면 토요일, 일요일과 이어 사흘을 쉬고, 월요일이면 앞의 토요일, 일요일과 이어 사흘을 쉰다.
   통과 조건이 연휴를 말하라고 하면, 답이 주말과 이어 쉰다는 것을 말해야 통과다. "사흘", "금토일", "주말까지 쭉"처럼 표현은 달라도 된다.
3. 연휴가 아니라고 하거나, 하루만 쉰다고 하거나, 연휴인지 말하지 않으면 2번의 실패다.
4. 통과 조건에 없는 내용은 판정하지 않는다.

예시
- 도구 결과: 12월 25일(금) 크리스마스 / 답: "25일이 금요일이라 주말까지 사흘 쉴 수 있어!" / 판정: 통과
- 도구 결과: 12월 25일(금) 크리스마스 / 답: "크리스마스가 있긴 한데 연휴는 아니야." / 판정: 실패
- 도구 결과: 2026-11-02부터 2026-11-30까지 공휴일이 없습니다. / 답: "이번 달엔 공휴일이 없어. 다음은 12월 25일 크리스마스야." / 판정: 실패 (12월 25일이 도구 결과에 없다)

먼저 판단 이유를 한두 문장으로 쓰고, 그다음 통과 여부를 정한다."""

    judge_prompt_user = """[사용자 메시지와 도구 결과]
{{input}}

[서비스의 답]
{{output}}

[이 항목의 통과 조건]
{{ground_truth}}"""

    try:
        from langfuse.api.evaluators.types import CreateEvaluatorRequest_LlmAsJudge, EvaluatorModelConfig
        
        # prompt를 dict로 직접 전달
        prompt_dict = {
            "messages": [
                {"role": "system", "content": judge_prompt_system},
                {"role": "user", "content": judge_prompt_user}
            ]
        }
        
        request = CreateEvaluatorRequest_LlmAsJudge(
            name="holiday-reply-judge",
            description="공휴일 답 품질 평가: 도구 결과 근거 여부, 연휴 언급 여부",
            prompt=prompt_dict,
            model_config=EvaluatorModelConfig(
                provider="openai",  # 또는 "anthropic", "gemini" 등
                model="gpt-4o-mini",
                temperature=0.0
            ),
            variable_mapping=[
                {"variable": "input", "source": "input"},
                {"variable": "output", "source": "output"},
                {"variable": "ground_truth", "source": "expected_output"}
            ]
        )
        
        evaluator = lf.api.evaluators.create(request=request)
        print(f"평가자 생성: {evaluator.name} v{evaluator.version} ({evaluator.id})")
        
    except Exception as e:
        print(f"평가자 생성 실패: {e}")
        import traceback
        traceback.print_exc()


def promote_prompt_to_production(prompt_name: str, version: int):
    """프롬프트 버전을 production 라벨로 승격."""
    try:
        lf.update_prompt(prompt_name, version=version, labels=["production"])
        print(f"프롬프트 승격: {prompt_name} v{version} -> production")
    except Exception as e:
        print(f"승격 실패: {e}")


def list_prompts_and_evaluators():
    """등록된 프롬프트와 평가자 목록."""
    print("\n=== 프롬프트 목록 ===")
    try:
        prompts = lf.get_prompts()
        for p in prompts.data:
            labels = [l.name for l in p.labels] if p.labels else []
            print(f"  {p.name} v{p.version} | 라벨: {labels} | 태그: {p.tags}")
    except Exception as e:
        print(f"프롬프트 조회 실패: {e}")

    print("\n=== 평가자 목록 ===")
    try:
        evaluators = lf.api.evaluators.list()
        for ev in evaluators.data:
            print(f"  {ev.name} v{ev.version} | ID: {ev.id} | 타입: {ev.type}")
    except Exception as e:
        print(f"평가자 조회 실패: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="프롬프트/평가자 관리")
    parser.add_argument("--create-prompts", action="store_true", help="답 생성 프롬프트 v1, v2 생성")
    parser.add_argument("--create-judge", action="store_true", help="LLM 심사 평가자 생성")
    parser.add_argument("--promote", nargs=2, metavar=("NAME", "VERSION"), help="프롬프트 버전을 production으로 승격")
    parser.add_argument("--list", action="store_true", help="목록 조회")
    args = parser.parse_args()

    if args.create_prompts:
        create_answer_prompt()
    if args.create_judge:
        create_judge_evaluator()
    if args.promote:
        promote_prompt_to_production(args.promote[0], int(args.promote[1]))
    if args.list:
        list_prompts_and_evaluators()

    if not any([args.create_prompts, args.create_judge, args.promote, args.list]):
        parser.print_help()


if __name__ == "__main__":
    main()