"""Langfuse 프롬프트 관리: router_prompt, build_persona 고정 규칙, EXTRACT_PROMPT를 Langfuse로 이동.

- 프롬프트 이름: router-prompt, persona-rules, memory-extract
- production/staging 라벨 지원
- 폴백: Langfuse 실패 시 코드 기본값 사용
- Generation에 프롬프트 연결로 트레이스에서 버전 확인 가능
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings
from app.tools import TOOLS
from app.persona import FIXED_RULES
from app.memory import EXTRACT_PROMPT, SUMMARY_PROMPT

load_settings()
lf = get_client()


def create_router_prompt():
    """router_prompt를 Langfuse 프롬프트로 생성 (변수: tools)."""
    # 현재 코드의 router_prompt를 템플릿으로 사용
    tools_template = "\n".join(f"- {name}: {t['desc']}. 인자 {t['args']}" for name, t in TOOLS.items())
    
    prompt_template = f"""너는 AI 친구 서비스에서 사용자 메시지에 답하기 전에 필요한 도구를 고르는 선택기다.
쓸 수 있는 도구:
{{tools}}

규칙:
- 도구가 필요 없는 일상 대화면 tools 를 빈 배열로 둔다. 대부분의 메시지는 여기에 해당한다.
- 꼭 필요한 도구만 최대 2개 고른다. 묻지 않은 정보의 도구는 덧붙이지 않는다(날씨만 물으면 get_weather 하나).
- 날씨나 미세먼지 이야기를 해도 예보를 묻는 게 아니라 경험이나 하소연이면 도구를 쓰지 않는다.
- location 은 메시지에 도시 이름이 있으면 그 도시, 없으면 사용자 지역이다.
- date 는 [달력]을 보고 정한 YYYY-MM-DD 날짜다. 날짜 표현이 없으면 오늘이다.
- create_reminder 는 사용자가 나중에 알려 달라고 분명히 부탁할 때만 고른다. 일정이 있다는 이야기만 하면 고르지 않는다.
- create_reminder 의 text 는 알려 줄 내용만 짧게 쓴다. 예: '우산 챙기기', '일찍 자기'.
- create_reminder 의 due_at 은 [지금] 시각과 시간대를 기준으로 계산한다. 시각 없이 날짜만 있으면 09:00, 아침 08:00, 점심 12:00, 저녁 19:00, 밤 21:00 으로 한다.
- 최근 대화를 보고 '그럼 모레는?' 같은 이어지는 질문의 도구와 인자를 정한다.
- get_holidays 는 '12월', '설날'처럼 기간을 말할 때만 from, to 를 채운다. '다음 공휴일', '남은 빨간 날'처럼 기간이 없으면 인자를 비워 둔다.
- '시험 전날', '출발 이틀 전'처럼 어떤 날을 기준으로 앞뒤를 말하면, 먼저 [달력]에서 기준이 되는 날의 날짜를 찾고 거기서 날짜를 빼거나 더한다.
- confidence 는 고른 결과가 맞을 확신을 0과 1 사이 숫자로 쓴다.
- reason 은 고른 이유를 한국어 한 문장으로 쓴다.
- 반드시 JSON 하나로만 답한다: {{"tools": [{{"name": "도구 이름", "args": {{인자}}}}], "confidence": 0과 1 사이 숫자, "reason": "이유"}}"""

    try:
        prompt = lf.create_prompt(
            name="router-prompt",
            prompt=prompt_template,
            labels=["production", "staging"],
            tags=["friend-agent", "tool-selection"],
            config={"type": "chat", "variables": ["tools"], "source": "router_prompt_v1"}
        )
        print(f"router-prompt 생성: v{prompt.version}")
        return prompt.version
    except Exception as e:
        print(f"router-prompt 생성 실패: {e}")
        return None


def create_persona_rules_prompt():
    """build_persona의 고정 규칙(FIXED_RULES)을 Langfuse 프롬프트로 생성."""
    prompt_template = FIXED_RULES

    try:
        prompt = lf.create_prompt(
            name="persona-rules",
            prompt=prompt_template,
            labels=["production", "staging"],
            tags=["friend-agent", "persona", "fixed-rules"],
            config={"type": "chat", "variables": [], "source": "FIXED_RULES_v1"}
        )
        print(f"persona-rules 생성: v{prompt.version}")
        return prompt.version
    except Exception as e:
        print(f"persona-rules 생성 실패: {e}")
        return None


def create_memory_extract_prompt():
    """EXTRACT_PROMPT를 Langfuse 프롬프트로 생성."""
    prompt_template = EXTRACT_PROMPT

    try:
        prompt = lf.create_prompt(
            name="memory-extract",
            prompt=prompt_template,
            labels=["production", "staging"],
            tags=["friend-agent", "memory", "extraction"],
            config={"type": "chat", "variables": [], "source": "EXTRACT_PROMPT_v1"}
        )
        print(f"memory-extract 생성: v{prompt.version}")
        return prompt.version
    except Exception as e:
        print(f"memory-extract 생성 실패: {e}")
        return None


def create_memory_summary_prompt():
    """SUMMARY_PROMPT를 Langfuse 프롬프트로 생성."""
    prompt_template = SUMMARY_PROMPT

    try:
        prompt = lf.create_prompt(
            name="memory-summary",
            prompt=prompt_template,
            labels=["production", "staging"],
            tags=["friend-agent", "memory", "summary"],
            config={"type": "chat", "variables": [], "source": "SUMMARY_PROMPT_v1"}
        )
        print(f"memory-summary 생성: v{prompt.version}")
        return prompt.version
    except Exception as e:
        print(f"memory-summary 생성 실패: {e}")
        return None


def get_prompt_with_fallback(prompt_name: str, label: str = None, default: str = None, **variables):
    """Langfuse에서 프롬프트 가져오기, 실패 시 기본값 사용."""
    try:
        if label:
            prompt = lf.get_prompt(prompt_name, label=label)
        else:
            prompt = lf.get_prompt(prompt_name)
        
        compiled = prompt.compile(**variables) if variables else prompt.prompt
        return compiled, prompt.version
    except Exception as e:
        print(f"프롬프트 {prompt_name} 조회 실패, 기본값 사용: {e}")
        if default:
            if variables:
                return default.format(**variables), "fallback"
            return default, "fallback"
        raise


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Langfuse 프롬프트 생성")
    parser.add_argument("--create-all", action="store_true", help="모든 프롬프트 생성")
    parser.add_argument("--list", action="store_true", help="목록 조회")
    args = parser.parse_args()

    if args.create_all:
        create_router_prompt()
        create_persona_rules_prompt()
        create_memory_extract_prompt()
        create_memory_summary_prompt()
    elif args.list:
        prompts = lf.get_prompts()
        for p in prompts.data:
            labels = [l.name for l in p.labels] if p.labels else []
            print(f"  {p.name} v{p.version} | 라벨: {labels} | 변수: {p.variables if hasattr(p, 'variables') else 'N/A'}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()