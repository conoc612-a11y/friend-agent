# Langfuse 평가 파이프라인 가이드

이 문서는 AI 친구 에이전트 서비스에 Langfuse를 붙여 평가 파이프라인을 구축하고 운영하는 전체 과정을 설명합니다.

---

## 1. 개요

### 문제 상황
- 같은 메시지라도 실행마다 답이 달라짐 (LLM 비결정성)
- 지시문 한 줄 수정으로 고쳐졌는지 대화 몇 개로는 확인 불가
- 수정사항이 다른 기능을 망가뜨리는지 모름 (회귀)
- 팀원 간 의견 갈림 (짧은 답 vs 현재 답) → 실제 사용자 선호 모름

### 해결: Langfuse 평가 파이프라인
```
트레이스 수집 → 오류 분석 → 데이터셋/평가자 생성 → 실험(프롬프트 비교) → A/B 테스트 → 배포 게이트
```

---

## 2. 빠른 시작

### 사전 준비
```bash
# .env에 Langfuse 키 설정 (이미 설정됨)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com

# 서버 실행
uvicorn app.main:app --port 8080
```

### 1단계: 트레이스 수집 (평가 데이터로)
```bash
# 평가 메시지 5개씩 보내기 (기본)
python scripts/send_eval_messages.py --limit 5

# 전체 보내기 (56개)
python scripts/send_eval_messages.py --limit 0
```

### 2단계: 데이터셋 & 평가자 생성
```bash
# 기존 트레이스로부터 데이터셋 생성
python scripts/create_dataset.py

# 평가자 등록
python scripts/create_dataset.py --only-evaluators
```

### 3단계: 실험 실행 (프롬프트 버전 비교)
```bash
# 4개 버전 비교 실험
python scripts/run_experiment.py

# 특정 버전만
python scripts/run_experiment.py --versions v1_original v4_both_rules
```

### 4단계: A/B 테스트 설정
```bash
# 실험 생성
python scripts/ab_test.py --setup

# 결과 분석 (데이터 쌓인 뒤)
python scripts/ab_test.py --analyze
```

### 5단계: CI/CD 배포 게이트
```bash
# 로컬에서 평가 실행
python scripts/ci_evaluate.py --dataset friend-agent-eval --threshold 0.8

# GitHub Actions 워크플로 생성
python scripts/ci_evaluate.py --generate-workflow
```

---

## 3. 스크립트 상세

### `send_eval_messages.py`
평가 데이터(`data/eval/*.jsonl`)의 메시지를 로컬 서버에 보내 트레이스를 쌓습니다.

| 옵션 | 설명 |
|------|------|
| `--limit N` | 보낼 개수 (0=전체, 기본 5) |
| `--user-id` | 사용할 사용자 ID (기본: eval-user) |

### `create_dataset.py`
Langfuse 트레이스로부터 데이터셋을 만들고 평가자를 등록합니다.

| 옵션 | 설명 |
|------|------|
| `--trace-ids` | 특정 트레이스만 사용 |
| `--dataset-name` | 데이터셋 이름 (기본: friend-agent-eval) |
| `--only-evaluators` | 평가자만 생성 |

**등록되는 평가자:**
| 이름 | 타입 | 검사 내용 |
|------|------|-----------|
| `tool_args_correctness` | 코드 | 도구 인자 정확도 (기간, 전날 계산 등) |
| `answer_grounded_in_tools` | LLM 심사 | 답의 날짜가 도구 결과에 근거함 |
| `holiday_weekend_connection` | 코드 | 금/월 공휴일 → 연휴 언급 |
| `reminder_day_before_correct` | 코드 | 'X 전날' = X 하루 전 |
| `user_feedback` | 사용자 | 좋아요/별로예요 점수 |

### `evaluators.py`
데이터셋 아이템에 대해 평가자들을 실행합니다.

```bash
# 전체 평가 실행
python scripts/evaluators.py

# 특정 평가자만
python scripts/evaluators.py --evaluators tool_args_correctness holiday_weekend_connection
```

### `run_experiment.py`
다른 프롬프트 버전으로 같은 데이터셋을 실행해 비교합니다.

**비교 버전:**
- `v1_original`: 원본 지시문
- `v2_day_before_rule`: "전날은 하루 앞" 규칙 추가
- `v3_date_range_rule`: 기간 명시 규칙 추가
- `v4_both_rules`: 둘 다 적용

### `manage_prompts.py`
지시문을 Langfuse 프롬프트로 버전 관리합니다.

```bash
# 코드 프롬프트 동기화
python scripts/manage_prompts.py --sync

# 실험용 변형 생성
python scripts/manage_prompts.py --create-variants

# 목록 조회
python scripts/manage_prompts.py --list

# 버전 승격 (실험 → 프로덕션)
python scripts/manage_prompts.py --promote router-prompt-v4-both 3

# 서버에서 조회 예시
prompt = get_prompt_for_version("router-prompt", "production")
```

### `ab_test.py`
실제 사용자 트래픽을 A/B로 나눠 비교합니다.

**서버 적용 예시 (app/main.py):**
```python
from scripts.ab_test import get_ab_test_prompt_version

@app.post("/api/chat")
def chat(req: ChatRequest, user: User = Depends(current_user)):
    version, system_prompt = get_ab_test_prompt_version(user.id, PROMPT_A, PROMPT_B)
    # system_prompt로 답 생성...
    # 트레이스 태그 추가
    lf.get_client().update_current_trace(tags=["ab-test", f"version-{version}"])
```

### `ci_evaluate.py`
배포 파이프라인에서 평가 실행 및 게이트 역할.

```bash
# 로컬 실행
python scripts/ci_evaluate.py --threshold 0.8

# GitHub Actions 워크플로 생성
python scripts/ci_evaluate.py --generate-workflow
```

**생성되는 워크플로 (`.github/workflows/evaluate-and-deploy.yml`):**
```yaml
jobs:
  evaluate:
    steps:
      - run: python scripts/ci_evaluate.py --dataset friend-agent-eval --threshold 0.8
  deploy:
    needs: evaluate
    if: success()
```

---

## 4. 오류 분석 워크플로

### 트레이스 읽기 순서
1. **도구 선택(generation)**: 고른 도구, 인자, 확신도
2. **도구 실행(span)**: 도구 이름, 인자, 결과 문장
3. **답 생성(generation)**: 지시문+도구 결과, 답, 토큰
4. **기억 정리(generation)**: 더한/지운 기억

### 실패 유형 분류표 예시
| 실패 유형 | 판정 기준 | 판정 방법 |
|-----------|-----------|-----------|
| 도구 인자 날짜/기간 틀림 | 기간 없는 질문→다음 공휴일 포함 기간, "X 전날"→X 하루 전 | 코드 |
| 도구 결과에 없는 날짜 말함 | 답의 공휴일 날짜 모두 도구 결과에 있음 | LLM 심사 |
| 연휴 잘못 말함 | 공휴일 금/월→주말과 이어져 사흘 쉼 언급 | LLM 심사 |
| 전날 계산 틀림 | "금요일 전날"=목요일로 저장 | 코드 |

---

## 5. 운영 팁

### 트레이스 필터링
- Langfuse Tracing에서 `tags:friend-agent` `environment:local` 필터
- `metadata.commit`으로 배포 버전별 분리

### 평가자 개선
- LLM 심사 프롬프트는 `evaluators.py`의 `evaluate_answer_grounded_in_tools` 참고
- False positive/negative가 많으면 프롬프트 수정 후 재실행

### 비용 관리
- 평가 데이터 56턴 × 모델 3호출 = 168호출/실험
- `LLM_PROVIDER=fake`로 코드 평가자만 돌려 비용 절약 가능

### 알림 설정
- Langfuse > Settings > Alerts에서 점수 임계값 알림 설정
- 예: `tool_args_correctness < 0.7` 시 Slack 알림

---

## 6. 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 데이터셋 아이템 0개 | 트레이스에 `user_id`, `session_id` 없음 | `app/tracing.py`의 `turn_attributes` 확인 |
| 평가자 점수 안 올라감 | Langfuse 키 없음 | `.env`의 `LANGFUSE_*` 키 확인 |
| 실험 결과 안 보임 | 데이터셋 아이템과 트레이스 매칭 안됨 | `metadata.trace_id`가 트레이스 ID와 일치하는지 확인 |
| A/B 테스트 그룹 불균형 | 솔트 값 고정 | `get_ab_test_prompt_version`의 솔트 변경 |

---

## 7. 다음 단계

1. **자동 회귀 탐지**: 새 배포 후 운영 트레이스에 LLM 심사 평가자 상시 실행
2. **사용자 세그먼트별 분석**: 신규/기존 사용자, 캐릭터별 만족도 분리
3. **프롬프트 옵티마이저**: Langfuse Prompt Playground로 자동 개선 제안
4. **비용/지연 예산**: 토큰 수, p99 지연 시간 임계값 추가