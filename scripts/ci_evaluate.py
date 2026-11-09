"""배포 파이프라인에 평가 걸기.

GitHub Actions 등 CI/CD에서 실험을 실행하고 기준 미달 시 배포를 막는다.
"""
import json
import sys
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langfuse import get_client
from app.config import load_settings

load_settings()
lf = get_client()


def run_evaluation_in_ci(dataset_name: str, threshold: float = 0.8) -> bool:
    """CI에서 평가 실행하고 통과 여부 반환."""
    print(f"=== CI 평가 실행: {dataset_name} ===")

    # 1. 데이터셋 조회
    dataset = None
    for d in lf.get_datasets().data:
        if d.name == dataset_name:
            dataset = d
            break

    if not dataset:
        print(f"데이터셋 '{dataset_name}'을 찾을 수 없습니다.")
        return False

    items = lf.get_dataset_items(dataset.id).data
    print(f"평가 대상: {len(items)}개 아이템")

    # 2. 현재 배포 버전의 트레이스 조회 (최신 커밋)
    commit_sha = os.environ.get("COMMIT_SHA", "unknown")
    traces_page = lf.get_traces(limit=100, tags=[f"commit:{commit_sha}"])
    traces = traces_page.data

    if not traces:
        print(f"커밋 {commit_sha}의 트레이스가 없습니다. 배포 후 트레이스가 쌓일 때까지 기다리세요.")
        return False

    print(f"조회된 트레이스: {len(traces)}개")

    # 3. 각 트레이스에 대해 평가자 실행
    from scripts.evaluators import (
        evaluate_tool_args_correctness,
        evaluate_holiday_weekend_connection,
        evaluate_reminder_day_before_correct,
    )

    evaluator_funcs = {
        "tool_args_correctness": evaluate_tool_args_correctness,
        "holiday_weekend_connection": evaluate_holiday_weekend_connection,
        "reminder_day_before_correct": evaluate_reminder_day_before_correct,
    }

    results = {name: [] for name in evaluator_funcs}

    for trace in traces:
        # 데이터셋 아이템과 매칭 (trace_id로)
        trace_id = trace.id
        matching_items = [item for item in items if item.metadata.get("trace_id") == trace_id]
        if not matching_items:
            continue

        item = matching_items[0]

        for name, func in evaluator_funcs.items():
            try:
                score = func(item, trace)
                results[name].append(score)
                # Langfuse에 점수 기록
                lf.create_score(
                    trace_id=trace_id,
                    name=name,
                    value=score,
                    data_type="NUMERIC",
                    comment=f"CI 평가: {commit_sha}"
                )
            except Exception as e:
                print(f"  평가 실패 ({name}): {e}")

    # 4. 결과 집계 및 임계값 검사
    print("\n=== 평가 결과 ===")
    all_passed = True

    for name, scores in results.items():
        if not scores:
            print(f"  {name}: 데이터 없음")
            continue
        avg_score = sum(scores) / len(scores)
        passed = avg_score >= threshold
        status = "통과" if passed else "실패"
        print(f"  {name}: {avg_score:.2%} ({len(scores)}개) - {status}")
        if not passed:
            all_passed = False

    # 전체 평균
    all_scores = [s for scores in results.values() for s in scores]
    if all_scores:
        overall = sum(all_scores) / len(all_scores)
        print(f"\n전체 평균: {overall:.2%} ({'통과' if overall >= threshold else '실패'})")
        if overall < threshold:
            all_passed = False

    return all_passed


def run_llm_judge_evaluation(dataset_name: str, evaluator_name: str = "answer_grounded_in_tools"):
    """LLM 심사 평가 실행 (Langfuse UI에서 수행 권장)."""
    print("LLM 심사 평가는 Langfuse 웹 UI의 Evaluators 탭에서 실행하세요.")
    print(f"1. Experiments > Create Experiment > {dataset_name} 선택")
    print(f"2. Evaluators > {evaluator_name} 선택 > Run")
    print("3. 결과가 Metrics 탭에 나타납니다.")


def create_github_actions_workflow():
    """GitHub Actions 워크플로 예시 생성."""
    workflow = '''
name: Evaluate and Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
  LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
  LANGFUSE_BASE_URL: ${{ secrets.LANGFUSE_BASE_URL }}
  LLM_API_KEY: ${{ secrets.LLM_API_KEY }}

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run evaluation
        run: |
          python scripts/ci_evaluate.py --dataset friend-agent-eval --threshold 0.8
        env:
          COMMIT_SHA: ${{ github.sha }}
      - name: Check evaluation result
        if: failure()
        run: echo "평가 기준 미달로 배포 중단" && exit 1

  deploy:
    needs: evaluate
    if: success()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy friend-agent --source . --region us-central1
        env:
          CLOUD_RUN_SERVICE: friend-agent
'''
    return workflow


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CI/CD 평가 실행")
    parser.add_argument("--dataset", default="friend-agent-eval", help="데이터셋 이름")
    parser.add_argument("--threshold", type=float, default=0.8, help="통과 임계값 (0~1)")
    parser.add_argument("--generate-workflow", action="store_true", help="GitHub Actions 워크플로 생성")
    args = parser.parse_args()

    if args.generate_workflow:
        workflow = create_github_actions_workflow()
        workflow_path = ROOT / ".github" / "workflows" / "evaluate-and-deploy.yml"
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(workflow.strip(), encoding="utf-8")
        print(f"워크플로 생성: {workflow_path}")
        return

    passed = run_evaluation_in_ci(args.dataset, args.threshold)
    if not passed:
        print("\n❌ 평가 기준 미달 - 배포 중단")
        sys.exit(1)
    else:
        print("\n✅ 평가 통과 - 배포 진행 가능")
        sys.exit(0)


if __name__ == "__main__":
    main()