# 상세 작업 지시서 — 폴더 구조 정리 및 Python import 최적화

## 1. 배경
현재 백엔드 구조에 `apps/api/app/api/routes` 형태의 중복 의미 경로가 존재한다. 바깥 `apps/api`는 이미 이 서비스가 API 서버임을 충분히 설명하고 있는데, 내부에 다시 `app/api/routes`가 들어 있어 구조 파악 시 혼동이 발생한다.

특히 아래 문제가 있다.
- `api`라는 이름이 상위 서비스명과 하위 패키지명에 중복 사용됨
- `routes`가 `api` 하위에 숨어 있어 실제 HTTP 엔드포인트 계층이 한 번 더 감춰짐
- import 경로가 불필요하게 길고 의미가 모호해짐
- 신규 참여자가 구조를 빠르게 이해하기 어려움

이번 작업의 핵심은 단순 미관 개선이 아니라, **라우팅 계층을 더 명확한 위치로 이동하고 import 경로를 예측 가능하게 정리하는 것**이다.

---

## 2. 목표

### 2.1 구조 목표
기존:

```text
apps/api/app/api/routes
```

변경:

```text
apps/api/app/routes
```

즉, `routes`를 `api` 밖으로 꺼내 `app` 바로 아래에 두는 방향으로 정리한다.

### 2.2 import 목표
전체 코드베이스에서 Python import를 다음 원칙으로 통일한다.
- 기준은 `app.*` 절대 import
- 상대 import 남용 금지
- 리팩토링 이후 import 경로가 짧고 역할이 바로 드러나야 함

예시:

```python
from app.routes.workspace import router
from app.services.job_service import JobService
from app.core.config import settings
```

---

## 3. 반드시 수행할 작업

### 3.1 라우팅 계층 이동
- `apps/api/app/api/routes` 디렉토리를 `apps/api/app/routes`로 이동한다.
- `routes` 내부 파일은 가급적 기존 이름을 유지하되, 필요한 경우 역할이 더 잘 드러나는 파일명으로 소폭 정리할 수 있다.
- `routes/__init__.py`가 필요하면 추가하여 import 진입점을 명확히 한다.

### 3.2 기존 `app/api` 패키지 정리
- `app/api` 아래에 `routes` 외 다른 파일이 있는지 확인한다.
- `app/api`가 단순 라우팅 컨테이너 역할이었다면 제거한다.
- 만약 `app/api`가 예외 처리, dependencies, middlewares, response helpers 같은 HTTP 관련 공통 계층을 담고 있다면 아래 중 하나를 택한다.
  - `app/http`로 개명 후 유지
  - 성격에 맞는 별도 패키지(`app/dependencies`, `app/middlewares` 등)로 분리
- 단, 이번 작업에서는 대공사보다 **중복 의미 제거와 명확성 확보**를 우선한다.

### 3.3 import 전면 수정
- `app.main`에서 router include import 경로를 새 위치 기준으로 모두 수정한다.
- 서비스, 스키마, 코어 모듈에서 라우터 경로를 참조하는 부분이 있다면 전부 새 경로로 바꾼다.
- 테스트 코드 import도 모두 함께 갱신한다.
- 상대 import가 섞여 있다면 가능한 범위에서 `app.*` 절대 import로 통일한다.

### 3.4 실행 경로 점검
다음이 모두 정상 동작해야 한다.
- FastAPI 앱 실행
- 테스트 실행
- migration 실행
- 개발 스크립트 실행

확인 대상 예시:
- `app/main.py`
- `tests/*`
- `migration/env.py`
- `run_migration.py`
- `scripts/*`

### 3.5 문서 반영
- README 또는 개발용 문서에 새 구조를 반영한다.
- 최소한 아래는 명시한다.
  - 라우팅 계층 위치
  - import 원칙 (`app.*` 절대 import)
  - 향후 패키지 추가 시 네이밍 원칙

---

## 4. 권장 구조
최소 목표 구조는 아래와 같다.

```text
apps/
  api/
    app/
      __init__.py
      main.py
      routes/
      services/
      schemas/
      core/
      adapters/
      agents/
      orchestrator/
      workers/
      team_runtime/
```

추가로 검토 가능한 항목:
- `models.py`, `conversation_models.py`를 `models/` 패키지로 정리할지 검토
- `conversations/`, `services/`, `schemas/` 간 책임 경계 점검

단, 이번 작업의 1차 목표는 어디까지나 `api/routes` 중복 제거와 import 정리다.

---

## 5. 리팩토링 원칙
- 기능 변경 금지. 구조와 import만 정리한다.
- 이름이 더 명확해지는 범위 내에서만 파일 이동/이름 변경을 허용한다.
- 한 번에 과도한 계층 분해는 하지 않는다.
- 테스트와 실행 검증 없이 완료 처리하지 않는다.

---

## 6. 완료 조건
아래를 모두 만족해야 완료다.

1. `apps/api/app/api/routes`가 제거되어 있다.
2. `apps/api/app/routes` 기준으로 라우터가 정상 동작한다.
3. 전체 import가 새 경로 기준으로 정리되어 있다.
4. 앱 실행이 정상이다.
5. 테스트가 통과한다.
6. migration 및 주요 스크립트가 깨지지 않는다.
7. 변경 후 구조와 규칙이 문서에 반영되어 있다.

---

## 7. 최종 보고 시 포함할 내용
- 변경 전/후 디렉토리 구조 요약
- 수정된 import 규칙 요약
- 실행 검증 결과
- 테스트 결과
- 남은 후속 리팩토링 후보

