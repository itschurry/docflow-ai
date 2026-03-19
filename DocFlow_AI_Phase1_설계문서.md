# DocFlow AI Phase 1 설계 문서

## 1. 문서 개요

- 문서명: DocFlow AI Phase 1 설계
- 목적: 사내 문서 자동화용 멀티 에이전트 오케스트레이터의 1차 개발 범위를 정의한다.
- 범위: 보고서 초안 생성, 사업비 엑셀 생성, PPT 초안 생성
- 산출물: `docx/markdown`, `xlsx`, `pptx`
- 비범위:
  - `.hwp` 직접 생성 완성형 지원
  - 고급 RAG/사내 지식베이스
  - 복잡한 승인 워크플로우
  - 팀 협업 코멘트 시스템
  - 기관별 양식 자동 대응

---

## 2. 제품 목표

DocFlow AI는 사용자의 자연어 요청과 참고자료를 입력받아 아래 작업을 반자동 수행한다.

1. 사업계획서 / 연구결과보고서 초안 작성
2. 사업비 계산 및 엑셀 산출물 생성
3. 발표용 PPT 초안 생성

핵심 목표는 “AI 채팅 앱”이 아니라, **문서 작업 공정 자동화 시스템**을 만드는 것이다.

---

## 3. Phase 1 핵심 원칙

1. **계산은 코드가 담당한다.**
   - 예산/사업비 계산은 LLM에 맡기지 않는다.
2. **문서 생성은 구조화 데이터 기반으로 처리한다.**
   - 자유 생성 텍스트를 바로 파일로 쓰지 않는다.
3. **에이전트는 역할 분리형으로 구성한다.**
   - Planner / Writer / Spreadsheet / Slide / Reviewer
4. **사람 검토를 전제로 한다.**
   - 최종 제출 전 반드시 사람이 수정/검토한다.
5. **모델 종속성을 낮춘다.**
   - OpenAI, Claude를 교체 가능한 LLM Provider 레이어 뒤에 둔다.

---

## 4. 사용자 시나리오

### 4.1 보고서 초안 생성
사용자 입력:
- “작년 사업계획서 참고해서 올해 연구결과보고서 초안 만들어줘”

시스템 처리:
- 참고자료 분석
- 문서 구조 추출
- 초안 생성
- 섹션별 결과 반환
- `md` 또는 `docx` 생성

### 4.2 사업비 엑셀 생성
사용자 입력:
- “이 기준으로 인건비/재료비 포함한 사업비 엑셀 만들어줘”

시스템 처리:
- 항목 정의 추출
- 룰 엔진 기반 계산
- 검산
- `xlsx` 생성

### 4.3 PPT 초안 생성
사용자 입력:
- “위 보고서 기준으로 12장 발표자료 만들어줘”

시스템 처리:
- 발표 목적 해석
- 슬라이드 아웃라인 생성
- 슬라이드별 제목/핵심 포인트/시각화 지시문 생성
- `pptx` 생성

---

## 5. Phase 1 범위 정의

### 포함
- 대화형 작업 요청
- 파일 업로드
- 업로드 문서 기반 텍스트 추출
- 보고서 초안 생성
- 엑셀 자동 생성
- PPT 자동 생성
- 작업 이력 저장
- 결과 파일 다운로드

### 제외
- 실시간 공동 편집
- `.hwp` 완전 자동 생성
- 외부 검색 기반 자료 조사
- 조직별 권한/결재 고도화
- 장기 메모리/RAG
- 자동 메일 발송

---

## 6. 상위 아키텍처

```text
[Web UI]
   |
   v
[API Server]
   |
   +--> [Orchestrator]
   |        |
   |        +--> [Planner Agent]
   |        +--> [Writer Agent]
   |        +--> [Spreadsheet Agent]
   |        +--> [Slide Agent]
   |        +--> [Reviewer Agent]
   |
   +--> [Document Parser]
   +--> [Template Engine]
   +--> [Budget Rule Engine]
   +--> [File Generator]
   |
   +--> [PostgreSQL]
   +--> [Object Storage]
   +--> [Redis / Job Queue]
```

---

## 7. 기술 스택 제안

## 7.1 Frontend
- Next.js
- TypeScript
- Tailwind CSS
- React Query 또는 TanStack Query

## 7.2 Backend
- FastAPI
- Pydantic
- SQLAlchemy
- Celery 또는 RQ
- Redis
- PostgreSQL

## 7.3 파일 처리
- Markdown: 내부 문자열/템플릿 처리
- Word: `python-docx`
- Excel: `openpyxl`, `xlsxwriter`
- PPT: `python-pptx`
- PDF 텍스트 추출: `pymupdf`
- DOCX 읽기: `python-docx`

## 7.4 LLM
- OpenAI API
- Claude API

---

## 8. 에이전트 구조

## 8.1 Planner Agent
역할:
- 사용자 요청을 작업 그래프로 분해
- 필요한 입력 부족 여부 판단
- 어떤 하위 에이전트를 호출할지 결정

입력:
- 사용자 요청
- 업로드 파일 메타데이터

출력 예:
```json
{
  "job_type": "report_and_ppt",
  "tasks": [
    "parse_reference_docs",
    "generate_report_outline",
    "generate_report_draft",
    "review_report",
    "generate_slide_outline",
    "generate_ppt"
  ]
}
```

## 8.2 Writer Agent
역할:
- 보고서 구조 생성
- 섹션별 초안 생성
- 문체 정리

입력:
- 문서 구조 요구사항
- 참고자료 요약

출력:
- 구조화된 보고서 JSON
- markdown/docx용 텍스트 블록

## 8.3 Spreadsheet Agent
역할:
- 예산 항목 추출
- 계산 입력 데이터 정리
- 코드 기반 계산 엔진에 전달
- 설명 문구 생성

주의:
- 실제 계산은 Budget Rule Engine이 수행한다.

## 8.4 Slide Agent
역할:
- 발표 목적에 맞는 슬라이드 구성 생성
- 페이지 수에 맞게 요약
- 시각 요소 지시문 생성

## 8.5 Reviewer Agent
역할:
- 누락 점검
- 중복/장황함 점검
- 위험 표현 표시
- 초안 품질 피드백 생성

---

## 9. LLM 라우팅 정책

### OpenAI 권장 작업
- 구조화 출력(JSON)
- 작업 분해
- 슬라이드 구조 생성
- 문서 생성용 필드 정리

### Claude 권장 작업
- 장문 요약
- 문장 다듬기
- 긴 보고서 초안 개선
- 자연스러운 문체 보정

### 공통 원칙
- 모델 호출은 `LLMProvider` 인터페이스 뒤에서만 수행한다.
- 상위 서비스는 특정 벤더 SDK를 직접 호출하지 않는다.

예시 인터페이스:
```python
class LLMProvider:
    async def generate_structured(self, prompt: str, schema: dict) -> dict: ...
    async def generate_text(self, prompt: str) -> str: ...
```

---

## 10. 핵심 도메인 모델

## 10.1 Project
- id
- name
- description
- created_at

## 10.2 Job
- id
- project_id
- job_type
- status
- requested_by
- created_at
- updated_at

## 10.3 Task
- id
- job_id
- task_type
- status
- input_payload
- output_payload
- started_at
- finished_at

## 10.4 FileAsset
- id
- project_id
- job_id
- file_type
- path
- source_type(upload/generated)
- created_at

## 10.5 PromptLog
- id
- task_id
- provider
- model
- prompt
- response
- created_at

---

## 11. 작업 상태 머신

```text
DRAFT
 -> QUEUED
 -> RUNNING
 -> REVIEW_REQUIRED
 -> COMPLETED

예외 상태:
FAILED
CANCELLED
```

### 전이 규칙
- 사용자 요청 생성 시 `DRAFT`
- 실행 시작 시 `QUEUED` -> `RUNNING`
- 사용자 확인이 필요하면 `REVIEW_REQUIRED`
- 생성 완료 시 `COMPLETED`
- 오류 발생 시 `FAILED`

---

## 12. 문서 처리 파이프라인

## 12.1 공통
1. 파일 업로드
2. 텍스트 추출
3. 메타데이터 저장
4. Planner가 작업 계획 생성
5. 하위 에이전트 실행
6. 결과 파일 생성
7. 사용자 검토 및 다운로드

## 12.2 보고서 파이프라인
1. 참고자료 파싱
2. 문서 구조 생성
3. 섹션 초안 생성
4. Reviewer 점검
5. markdown/docx 생성

## 12.3 엑셀 파이프라인
1. 예산 항목 추출
2. 계산 입력 정규화
3. Rule Engine 계산
4. 검산 결과 생성
5. xlsx 렌더링

## 12.4 PPT 파이프라인
1. 발표 목적 분류
2. 슬라이드 개요 생성
3. 슬라이드별 본문 생성
4. 템플릿 반영
5. pptx 렌더링

---

## 13. 구조화 스키마 예시

## 13.1 보고서 스키마
```json
{
  "title": "연구 결과 보고서",
  "sections": [
    {
      "heading": "1. 과제 개요",
      "summary": "요약 문단",
      "bullets": ["항목1", "항목2"]
    }
  ]
}
```

## 13.2 예산 스키마
```json
{
  "items": [
    {
      "category": "인건비",
      "name": "연구원 A",
      "unit_cost": 5000000,
      "months": 6,
      "rate": 0.5
    }
  ]
}
```

## 13.3 PPT 스키마
```json
{
  "slides": [
    {
      "title": "과제 개요",
      "message": "핵심 메시지",
      "bullets": ["A", "B", "C"],
      "visual_hint": "공정 흐름도"
    }
  ]
}
```

---

## 14. Budget Rule Engine 설계

목적:
- 예산 계산의 결정성을 확보한다.
- LLM 출력은 입력 후보일 뿐, 계산의 진실은 코드가 가진다.

필수 기능:
- 항목별 공식 정의
- 반올림 규칙
- 합계 계산
- 검산
- 오류 검출

예:
```python
amount = unit_cost * months * rate
```

향후 확장:
- 직접비/간접비 규칙
- 정부지원금/민간부담금 비율
- 기관별 계산 룰셋

---

## 15. 템플릿 시스템

Phase 1에서는 단순 템플릿만 지원한다.

### 보고서
- 기본 보고서 docx 템플릿 1종
- 제목/소제목/본문 스타일 지정

### 엑셀
- 사업비 산출 템플릿 1종
- 입력 시트 / 계산 시트 / 요약 시트

### PPT
- 기본 발표 템플릿 1종
- 표지 / 목차 / 본문 / 마무리 슬라이드 레이아웃

---

## 16. API 초안

## 16.1 프로젝트 생성
`POST /api/projects`

## 16.2 파일 업로드
`POST /api/projects/{project_id}/files`

## 16.3 작업 생성
`POST /api/projects/{project_id}/jobs`

요청 예:
```json
{
  "request": "작년 사업계획서 참고해서 결과보고서와 발표자료 초안을 만들어줘",
  "output_types": ["report", "ppt"]
}
```

## 16.4 작업 조회
`GET /api/jobs/{job_id}`

## 16.5 작업 결과 조회
`GET /api/jobs/{job_id}/artifacts`

## 16.6 재생성 요청
`POST /api/jobs/{job_id}/retry`

---

## 17. DB 테이블 초안

### projects
- id
- name
- description
- created_at

### jobs
- id
- project_id
- job_type
- request_text
- status
- created_by
- created_at
- updated_at

### tasks
- id
- job_id
- task_type
- status
- input_payload_json
- output_payload_json
- error_message
- started_at
- finished_at

### files
- id
- project_id
- job_id
- original_name
- stored_path
- mime_type
- size
- source_type
- created_at

### prompt_logs
- id
- task_id
- provider
- model
- prompt_text
- response_text
- created_at

---

## 18. 예외 처리 원칙

1. LLM 응답이 JSON 스키마를 깨면 재시도 1회
2. 파일 생성 실패 시 작업 상태를 `FAILED`로 기록
3. 입력 부족 시 작업을 중단하지 말고 “필수 입력 부족” 결과를 생성
4. 모델 장애 시 대체 provider fallback 가능하도록 설계
5. 사용자에게는 내부 스택트레이스가 아니라 요약 오류 메시지만 노출

---

## 19. 보안 및 운영 원칙

1. 업로드 파일은 프로젝트 단위로 분리 저장
2. API Key는 서버 환경변수 또는 시크릿 매니저 사용
3. 프롬프트/응답 로그는 내부 관리자만 조회
4. 민감 문서는 외부 로그 시스템에 원문 전송 금지
5. 결과 파일 다운로드 URL은 만료형으로 제공

---

## 20. 디렉토리 구조 예시

```text
docflow-ai/
├─ apps/
│  ├─ web/
│  └─ api/
├─ services/
│  ├─ orchestrator/
│  ├─ llm/
│  ├─ document_parser/
│  ├─ template_engine/
│  ├─ budget_engine/
│  └─ file_generator/
├─ workers/
│  ├─ report_worker/
│  ├─ spreadsheet_worker/
│  └─ slide_worker/
├─ packages/
│  ├─ schemas/
│  └─ utils/
├─ templates/
│  ├─ report/
│  ├─ excel/
│  └─ ppt/
└─ docs/
```

---

## 21. MVP 개발 순서

### Step 1
- 프로젝트/파일 업로드 API
- 기본 DB 스키마
- 작업/태스크 상태 관리

### Step 2
- 문서 파서 구현
- Planner Agent 구현
- LLM Provider 추상화

### Step 3
- Writer Agent + markdown/docx 생성

### Step 4
- Spreadsheet Agent + Budget Rule Engine + xlsx 생성

### Step 5
- Slide Agent + pptx 생성

### Step 6
- Reviewer Agent
- 작업 이력 조회 UI
- 다운로드 UI

---

## 22. 완료 기준

Phase 1 완료 조건:
1. 업로드 문서를 바탕으로 보고서 초안을 생성할 수 있다.
2. 예산 입력값으로부터 엑셀 파일을 생성할 수 있다.
3. 보고서 또는 요청문 기반 PPT 초안을 생성할 수 있다.
4. 작업 상태와 결과 파일을 UI에서 조회할 수 있다.
5. OpenAI 또는 Claude 중 하나 이상 장애 시 최소 기능 저하로 운영 가능하다.

---

## 23. 이후 확장 포인트

- `.hwp` 템플릿 병합 엔진
- 승인/결재 플로우
- 지식베이스 검색
- 팀 단위 권한 관리
- 과거 문서 재활용 추천
- 기관별 제출 양식 대응
- 문서 diff 비교
- 슬라이드 디자인 고도화

---

## 24. 한 줄 요약

DocFlow AI Phase 1은  
**“업로드 문서 + 자연어 요청을 기반으로 보고서, 엑셀, PPT 초안을 생성하는 역할 분리형 문서 오케스트레이터”**  
를 목표로 한다.
