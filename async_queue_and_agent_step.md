# DocFlow AI 작업 지시서
## Feature: Async Job Queue + Agent Step Visualization

---

# 1. 비동기 Job Queue 구조 도입

## 목표
- 사용자 요청 → 즉시 응답 (Job ID 반환)
- 실제 작업은 백그라운드에서 수행
- 상태 추적 가능 (pending → running → done → failed)

## 아키텍처

[Client]
   ↓
[API Server]
   ↓ (enqueue)
[Queue Layer (Redis)]
   ↓
[Worker]
   ↓
[DB / Storage]

## 구현 요구사항

### 1. Queue 선택
- 기본: Redis + Celery
- 대안: RQ 또는 자체 asyncio queue

### 2. Job 모델 정의

```python
class Job(Base):
    id: str
    status: str  # pending, running, done, failed
    progress: int  # 0~100
    result: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime
```

### 3. API 설계

- POST /jobs
  - 요청 생성
  - Job enqueue
  - job_id 반환

- GET /jobs/{job_id}
  - 상태 조회
  - progress 포함

### 4. Worker 구조

- Agent 실행 로직을 worker로 이동
- 각 step 실행 시 progress 업데이트

```python
update_job(job_id, status="running", progress=30)
```

### 5. 상태 전이

- pending → running → done
- 실패 시 → failed

---

# 2. Agent Step 시각화

## 목표
- agent 내부 실행 흐름을 UI에서 실시간 표시
- "지금 뭐 하는지" 보이게 만드는 핵심 기능

## 데이터 구조

### Step 모델

```python
class AgentStep(Base):
    id: str
    job_id: str
    step_name: str
    status: str  # pending, running, done, failed
    started_at: datetime | None
    finished_at: datetime | None
    output: dict | None
```

---

## 이벤트 흐름

Worker 실행 중:

1. step 시작
```python
create_step(job_id, step_name, status="running")
```

2. step 완료
```python
update_step(step_id, status="done", output=...)
```

3. 실패 시
```python
update_step(step_id, status="failed", error=...)
```

---

## 실시간 전달 구조

### 방식 1: WebSocket (권장)
- /ws/jobs/{job_id}
- step 이벤트 push

### 방식 2: Polling
- 1~2초 간격 GET /jobs/{job_id}/steps

---

## UI 요구사항

### 1. Step Timeline UI

- 순서대로 나열
- 상태별 색상:
  - pending: 회색
  - running: 파랑
  - done: 초록
  - failed: 빨강

### 2. 실행 로그 표시

- step 클릭 시 output 표시

### 3. Progress Bar

- job.progress 기반

---

## 핵심 UX

- 요청 후:
  - 즉시 Job 생성됨
  - UI는 "작업 진행 중" 상태 유지
- Step이 하나씩 채워지며 진행됨
- 사용자는 "AI가 일하는 과정"을 확인 가능

---

# 3. 최소 구현 우선순위

1. Redis 기반 Queue
2. Job 상태 API
3. Worker 분리
4. Step 기록 테이블
5. Polling 기반 UI
6. 이후 WebSocket 확장

---

# 끝

이 두 기능은 DocFlow의 '겉보기 성능'과 '신뢰감'을 결정하는 핵심이다.
무조건 먼저 붙여라.

