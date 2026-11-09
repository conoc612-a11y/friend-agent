"""Langfuse 네이티브 run_experiment API로 실험 실행.

Langfuse에 저장된 프롬프트로 데이터셋 아이템마다 답을 생성하고,
등록된 평가자(LLM-as-a-Judge 포함)로 자동 평가한다.

실행:
  python scripts/run_langfuse_experiment.py --prompt holiday-reply-answer --version 1 --dataset holiday-reply --evaluator holiday-reply-judge
  python scripts/run_langfuse_experiment.py --prompt holiday-reply-answer --version 2 --dataset holiday-reply --evaluator holiday-reply-judge
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


def run_experiment(prompt_name: str, prompt_version: int, dataset_name: str, 
                   evaluator_names: list, experiment_name: str, run_name: str = None):
    """Langfuse run_experiment으로 실험 실행."""

    # 데이터셋 조회
    try:
        dataset = lf.get_dataset(dataset_name)
        print(f"데이터셋: {dataset.name} ({len(dataset.items)}개 아이템)")
    except Exception as e:
        print(f"데이터셋 조회 실패: {e}")
        return

    # 프롬프트 조회
    try:
        prompt = lf.get_prompt(prompt_name, version=prompt_version)
        print(f"프롬프트: {prompt.name} v{prompt.version}")
        print(f"  변수: {prompt.variables if hasattr(prompt, 'variables') else 'N/A'}")
    except Exception as e:
        print(f"프롬프트 조회 실패: {e}")
        return

    # 평가자 조회
    evaluators = []
    for ev_name in evaluator_names:
        try:
            ev = lf.api.evaluators.get(ev_name)
            evaluators.append(ev)
            print(f"평가자: {ev.name} ({ev.id})")
        except Exception as e:
            print(f"평가자 {ev_name} 조회 실패: {e}")

    if not evaluators:
        print("사용 가능한 평가자가 없습니다.")
        return

    # Task 함수 정의 - 프롬프트로 답 생성
    def task(*, item, **kwargs):
        """데이터셋 아이템으로 답 생성."""
        # 프롬프트 컴파일 (변수 치환)
        compiled = prompt.compile(**item.input)
        
        # LLM 호출 (Langfuse가 자동으로 tracing)
        # 여기서는 설정에 따라 LLM을 호출해야 함
        # run_experiment은 task 함수의 반환값을 output으로 사용
        
        # 실제로는 llm.complete_text나 chat_stream을 호출해야 하지만
        # Langfuse run_experiment은 task가 output을 반환하면 자동으로 generation trace를 만듦
        # 우리는 fake 모드이므로 간단한 시뮬레이션
        
        from app.config import load_settings
        from app.llm import make_llm
        from app.persona import DEFAULT_CHARACTER
        from app import when
        
        settings = load_settings()
        llm = make_llm(settings)
        
        # 컴파일된 프롬프트에서 messages 추출
        messages = compiled if isinstance(compiled, list) else [{"role": "user", "content": compiled}]
        
        # LLM 호출
        if hasattr(llm, 'chat_stream'):
            reply = ""
            for chunk in llm.chat_stream(messages):
                reply += chunk
        else:
            reply = llm.complete_text(messages)
        
        return reply

    # 평가자 함수 변환
    def make_evaluator(eval_name):
        def evaluator(*, input, output, expected_output, metadata, **kwargs):
            # Langfuse에 등록된 평가자 호출
            # 실제로는 lf.api.evaluators.run 등으로 호출하지만
            # run_experiment의 evaluators 파라미터에는 함수나 evaluator 객체 전달 가능
            pass
        return evaluator

    # 실험 실행
    print(f"\n실험 시작: {experiment_name}")
    if run_name:
        print(f"  실행 이름: {run_name}")

    try:
        result = lf.run_experiment(
            name=experiment_name,
            run_name=run_name or f"{experiment_name}-{prompt_version}",
            description=f"{prompt_name} v{prompt_version}로 {dataset_name} 평가",
            data=dataset.items,
            task=task,
            evaluators=evaluators,  # evaluator 객체 전달
            max_concurrency=5,
            metadata={
                "prompt": prompt_name,
                "prompt_version": prompt_version,
                "dataset": dataset_name,
            }
        )

        print(f"\n실험 완료!")
        print(f"  실험 ID: {result.experiment_id}")
        print(f"  실행 이름: {result.run_name}")
        print(f"  처리된 아이템: {len(result.item_results)}개")
        print(f"  Langfuse URL: {result.experiment_url}")

        # 결과 요약
        for ev_name in evaluator_names:
            scores = [r for r in result.item_results for e in r.evaluations if e.name == ev_name]
            if scores:
                pass_scores = sum(1 for s in scores if s.value == 1 or s.value is True)
                total = len(scores)
                print(f"  {ev_name}: {pass_scores}/{total} 통과 ({pass_scores/total*100:.1f}%)")

        return result

    except Exception as e:
        print(f"실험 실행 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(description="Langfuse 네이티브 실험 실행")
    parser.add_argument("--prompt", default="holiday-reply-answer", help="프롬프트 이름")
    parser.add_argument("--version", type=int, default=1, help="프롬프트 버전")
    parser.add_argument("--dataset", default="holiday-reply", help="데이터셋 이름")
    parser.add_argument("--evaluator", nargs="+", default=["holiday-reply-judge"], help="평가자 이름들")
    parser.add_argument("--experiment-name", default="holiday-reply-eval", help="실험 이름")
    parser.add_argument("--run-name", help="실행 이름 (기본: 실험명-버전)")
    args = parser.parse_args()

    run_experiment(
        prompt_name=args.prompt,
        prompt_version=args.version,
        dataset_name=args.dataset,
        evaluator_names=args.evaluator,
        experiment_name=args.experiment_name,
        run_name=args.run_name
    )


if __name__ == "__main__":
    main()