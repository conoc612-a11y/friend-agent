"""A/B 테스트 설정 및 분석.

실제 사용자를 두 그룹으로 나눠 다른 프롬프트 버전을 적용하고 반응을 비교한다.
Langfuse의 세션 태그와 Metrics 탭을 활용한다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings

load_settings()
lf = get_client()


def setup_ab_test(prompt_a_name: str, prompt_b_name: str, experiment_name: str = "ab-test"):
    """A/B 테스트 실험 설정."""
    # 실험 생성
    experiment = lf.create_experiment(
        name=experiment_name,
        dataset_name="",  # 실시간 트래픽이므로 데이터셋 없음
        description=f"A/B 테스트: {prompt_a_name} vs {prompt_b_name}"
    )
    print(f"A/B 테스트 실험 생성: {experiment.id}")
    print(f"Langfuse Experiments 탭에서 '{experiment_name}' 확인")
    return experiment.id


def get_ab_test_prompt_version(user_id: str, prompt_a: str, prompt_b: str, salt: str = "ab-test-2024") -> str:
    """사용자 ID 기반으로 A/B 그룹 결정 (결정론적)."""
    import hashlib
    hash_input = f"{user_id}:{salt}".encode()
    hash_val = int(hashlib.md5(hash_input).hexdigest(), 16)
    return prompt_a if hash_val % 2 == 0 else prompt_b


def create_ab_test_middleware():
    """FastAPI 미들웨어 예시: 요청마다 A/B 버전 할당."""
    code = '''
from langfuse import get_client

def get_prompt_version(user_id: str) -> tuple[str, str]:
    """A/B 버전 결정. (버전명, 프롬프트) 반환."""
    import hashlib
    # 실제로는 설정/DB에서 프롬프트 조회
    prompt_a = "v1_original_prompt_text"
    prompt_b = "v4_both_rules_prompt_text"

    hash_val = int(hashlib.md5(f"{user_id}:ab-test-2024".encode()).hexdigest(), 16)
    version = "A" if hash_val % 2 == 0 else "B"
    prompt = prompt_a if version == "A" else prompt_b
    return version, prompt


# app/main.py의 chat 엔드포인트에서 사용 예시:
# version, system_prompt = get_prompt_version(user.id)
# ... system_prompt로 답 생성 ...
# 트레이스에 태그 추가: lf.get_client().update_current_trace(tags=["ab-test", f"version-{version}"])
'''
    return code


def analyze_ab_test(experiment_name: str = "ab-test"):
    """A/B 테스트 결과 분석."""
    # 실험 조회
    experiments = lf.get_experiments()
    experiment = None
    for exp in experiments.data:
        if exp.name == experiment_name:
            experiment = exp
            break

    if not experiment:
        print(f"실험 '{experiment_name}'을 찾을 수 없습니다.")
        return

    print(f"=== A/B 테스트 분석: {experiment.name} ===")

    # 트레이스에서 버전별 메트릭 수집
    # Langfuse UI의 Experiments > Metrics 탭에서 확인 가능
    # 여기서는 API로 점수 조회 예시

    scores_page = lf.get_scores(name="user-feedback")  # 사용자 피드백 점수
    version_a_scores = []
    version_b_scores = []

    for score in scores_page.data:
        trace = lf.get_trace(score.trace_id)
        version_tag = next((t for t in trace.tags if t.startswith("version-")), None)
        if version_tag == "version-A":
            version_a_scores.append(score.value)
        elif version_tag == "version-B":
            version_b_scores.append(score.value)

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    print(f"버전 A (원본): 좋아요 비율 {avg(version_a_scores):.2%} ({len(version_a_scores)}개)")
    print(f"버전 B (수정): 좋아요 비율 {avg(version_b_scores):.2%} ({len(version_b_scores)}개)")

    # 통계적 유의성 검정 (간단 버전)
    import math
    if version_a_scores and version_b_scores:
        n1, n2 = len(version_a_scores), len(version_b_scores)
        p1, p2 = avg(version_a_scores), avg(version_b_scores)
        pooled_p = (p1 * n1 + p2 * n2) / (n1 + n2)
        se = math.sqrt(pooled_p * (1 - pooled_p) * (1/n1 + 1/n2))
        if se > 0:
            z = (p1 - p2) / se
            print(f"Z-점수: {z:.2f} ({'유의함' if abs(z) > 1.96 else '유의하지 않음', p<0.05})")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="A/B 테스트 관리")
    parser.add_argument("--setup", action="store_true", help="A/B 테스트 실험 생성")
    parser.add_argument("--analyze", action="store_true", help="결과 분석")
    parser.add_argument("--prompt-a", default="v1_original", help="버전 A 이름")
    parser.add_argument("--prompt-b", default="v4_both_rules", help="버전 B 이름")
    parser.add_argument("--experiment", default="ab-test", help="실험 이름")
    args = parser.parse_args()

    if args.setup:
        setup_ab_test(args.prompt_a, args.prompt_b, args.experiment)
    elif args.analyze:
        analyze_ab_test(args.experiment)
    else:
        print("사용법: --setup 또는 --analyze 중 하나를 지정하세요.")
        print("미들웨어 코드 예시:")
        print(create_ab_test_middleware())


if __name__ == "__main__":
    main()