"""Langfuse 프롬프트 관리.

지시문(시스템 프롬프트)을 Langfuse에서 버전으로 관리하고 라벨로 배포 버전 지정.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings
from app.persona import build_persona, DEFAULT_CHARACTER, FIXED_RULES
from app.router import router_prompt, TOOLS

load_settings()
lf = get_client()


def get_current_prompts():
    """현재 코드의 프롬프트들을 구성."""
    # 캐릭터 지시문
    persona_prompt = build_persona(DEFAULT_CHARACTER)

    # 라우터(도구 선택) 지시문
    router = router_prompt()

    # 답 생성용 기본 시스템 프롬프트 (캐릭터 + 달력 + 도구 결과 자리)
    base_system = f"""{FIXED_RULES}

[캐릭터]
너는 아래 캐릭터로 사용자와 대화한다.
{persona_prompt}

[지금] {{now}} ({{timezone}}), 사용자 지역: {{location}}
[달력]
{{calendar_hint}}

[기억 메모: 사용자에 대해 알고 있는 것]
{{memory_block}}

[도구 결과]
{{tool_results}}

{{clarify_note}}"""

    return {
        "router-prompt": router,
        "system-prompt-base": base_system,
        "persona-prompt": persona_prompt,
    }


def sync_prompts_to_langfuse():
    """현재 코드의 프롬프트를 Langfuse에 동기화."""
    prompts = get_current_prompts()
    commit = load_settings().commit

    for name, prompt in prompts.items():
        try:
            # 기존 프롬프트 확인
            existing = None
            try:
                prompts_list = lf.get_prompts()
                for p in prompts_list.data:
                    if p.name == name:
                        existing = p
                        break
            except Exception:
                pass

            if existing:
                # 새 버전 생성
                new_version = lf.create_prompt(
                    name=name,
                    prompt=prompt,
                    labels=["production"] if "production" not in [l.name for l in existing.labels] else [],
                    tags=["friend-agent", "auto-synced"],
                    config={"commit": commit, "source": "code"}
                )
                print(f"프롬프트 업데이트: {name} v{new_version.version}")
            else:
                # 새로 생성
                new_prompt = lf.create_prompt(
                    name=name,
                    prompt=prompt,
                    labels=["production"],
                    tags=["friend-agent", "auto-synced"],
                    config={"commit": commit, "source": "code"}
                )
                print(f"프롬프트 생성: {name} v{new_prompt.version}")

        except Exception as e:
            print(f"프롬프트 동기화 실패 ({name}): {e}")


def create_prompt_variants():
    """비교용 프롬프트 변형 생성 (실험용)."""
    base_router = router_prompt()

    variants = {
        "router-prompt-v1-original": base_router,
        "router-prompt-v2-day-before": base_router + """

- 'X 전날' 표현(예: '금요일 시험 전날', '크리스마스 전날')은 X의 하루 앞 날짜로 계산한다.
  예: 금요일 전날=목요일, 크리스마스(12/25) 전날=12/24.""",
        "router-prompt-v3-date-range": base_router + """

- '다음 공휴일 언제야?', '올해 남은 빨간 날' 같이 기간을 명시하지 않은 공휴일 질문은
  오늘부터 앞으로 1년 내의 공휴일을 찾도록 from/to를 비워 둔다.
- 'X월 연휴 있어?'처럼 달만 지정된 질문은 그 달의 1일부터 말일까지로 기간을 잡는다.""",
        "router-prompt-v4-both": base_router + """

- 'X 전날' 표현(예: '금요일 시험 전날', '크리스마스 전날')은 X의 하루 앞 날짜로 계산한다.
  예: 금요일 전날=목요일, 크리스마스(12/25) 전날=12/24.

- '다음 공휴일 언제야?', '올해 남은 빨간 날' 같이 기간을 명시하지 않은 공휴일 질문은
  오늘부터 앞으로 1년 내의 공휴일을 찾도록 from/to를 비워 둔다.
- 'X월 연휴 있어?'처럼 달만 지정된 질문은 그 달의 1일부터 말일까지로 기간을 잡는다.""",
    }

    commit = load_settings().commit

    for name, prompt in variants.items():
        try:
            new_prompt = lf.create_prompt(
                name=name,
                prompt=prompt,
                labels=["experiment"],
                tags=["friend-agent", "variant", "router"],
                config={"commit": commit, "source": "code", "variant": name}
            )
            print(f"변형 생성: {name} v{new_prompt.version}")
        except Exception as e:
            print(f"변형 생성 실패 ({name}): {e}")


def get_prompt_for_version(prompt_name: str, label: str = "production") -> str:
    """라벨로 프롬프트 조회 (서버에서 사용)."""
    try:
        prompt = lf.get_prompt(prompt_name, label=label)
        return prompt.prompt
    except Exception as e:
        print(f"프롬프트 조회 실패 ({prompt_name}@{label}): {e}")
        return None


def promote_prompt_version(prompt_name: str, version: int, label: str = "production"):
    """특정 버전을 라벨로 승격."""
    try:
        lf.update_prompt(prompt_name, version=version, labels=[label])
        print(f"프롬프트 승격: {prompt_name} v{version} -> {label}")
    except Exception as e:
        print(f"승격 실패: {e}")


def list_prompts():
    """등록된 프롬프트 목록."""
    prompts = lf.get_prompts()
    for p in prompts.data:
        labels = [l.name for l in p.labels]
        print(f"  {p.name} v{p.version} | 라벨: {labels} | 태그: {p.tags} | 수정: {p.updated_at}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="프롬프트 관리")
    parser.add_argument("--sync", action="store_true", help="코드 프롬프트를 Langfuse에 동기화")
    parser.add_argument("--create-variants", action="store_true", help="실험용 변형 생성")
    parser.add_argument("--list", action="store_true", help="프롬프트 목록")
    parser.add_argument("--promote", nargs=2, metavar=("NAME", "VERSION"), help="버전 승격")
    parser.add_argument("--get", nargs=2, metavar=("NAME", "LABEL"), help="프롬프트 조회")
    args = parser.parse_args()

    if args.sync:
        sync_prompts_to_langfuse()
    elif args.create_variants:
        create_prompt_variants()
    elif args.list:
        list_prompts()
    elif args.promote:
        promote_prompt_version(args.promote[0], int(args.promote[1]))
    elif args.get:
        prompt = get_prompt_for_version(args.get[0], args.get[1])
        if prompt:
            print(prompt[:500] + ("..." if len(prompt) > 500 else ""))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()