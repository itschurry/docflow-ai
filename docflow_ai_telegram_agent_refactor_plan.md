# DocFlow AI → 텔레그램 멀티 에이전트 플랫폼 전환 계획

## 목표
기존 `docflow-ai`를 버리지 않고 확장해서,  
**텔레그램 그룹/토픽 기반 AI 에이전트 작업실**로 전환한다.

핵심 UX:
- 사용자는 텔레그램 방에서 작업 지시
- 여러 에이전트가 서로 멘션하며 협업하는 것처럼 보임
- 실제 백엔드는 중앙 오케스트레이터가 제어
- 최종 산출물은 md / docx / xlsx / pptx 로 생성 및 회신

---

## 전환 방향
현재 구조는 **문서 생성 백엔드**에 가깝다.  
목표 구조는 **문서 생성 + 멀티 에이전트 오케스트레이션 플랫폼**이다.

즉:
- 유지: FastAPI, DB, artifact pipeline, provider routing, jobs
- 추가: Telegram adapter, conversation/message model, orchestrator, agent runtime
- 확장: 웹 UI는 나중 단계, 1차는 Telegram 우선

---

## 아키텍처 개요

```text
Telegram Group / Topic
    ↓
Telegram Adapter
    ↓
Agent Orchestrator
    ├─ Planner Agent
    ├─ Writer Agent
    ├─ Critic Agent
    ├─ Coder Agent
    ├─ Reviewer Agent
    └─ Manager Agent
    ↓
LLM Providers / Copilot Worker / Tool Workers
    ↓
Artifacts + DB + Logs
```

---

## 반드시 지켜야 할 원칙

1. **멀티봇처럼 보여도 실제 제어는 중앙 오케스트레이터가 한다**
2. 텔레그램은 **입력/알림/협업 UX 레이어**
3. 모델 호출과 상태 관리는 **서버가 책임진다**
4. 에이전트는 “실제 독립 봇”이 아니라 **역할(role) 기반 런타임**
5. 문서 생성 기능은 버리지 말고 **최종 산출 단계로 재사용**

---

## 1차 목표 범위 (MVP)

### 구현 범위
- 텔레그램 그룹 메시지 수신
- 특정 멘션/명령 감지
- conversation 생성
- agent turn 실행
- agent별 발화 메시지 텔레그램 전송
- 결과 요약
- 아티팩트 생성 요청 연결

### 제외 범위
- 복잡한 웹 UI
- 실시간 공동 편집
- 다중 조직/권한 체계
- 정교한 billing/usage 대시보드
- 완전 자동 self-play 장시간 실행

---

## 추천 디렉토리 변경안

```text
apps/
  api/
    app/
      adapters/
        telegram/
          bot.py
          handlers.py
          formatter.py
          commands.py
      orchestrator/
        engine.py
        router.py
        policies.py
        state_machine.py
      agents/
        base.py
        planner.py
        writer.py
        critic.py
        coder.py
        reviewer.py
        manager.py
      conversations/
        service.py
        selectors.py
        serializer.py
      models/
        conversation.py
        participant.py
        message.py
        mention.py
        agent_run.py
      workers/
        telegram_dispatcher.py
        artifact_worker.py
        llm_worker.py
      providers/
        openai.py
        anthropic.py
        copilot.py
```

기존 `jobs`, `artifacts`, `providers`는 유지하되, conversation 축을 추가한다.

---

## 데이터 모델 추가

### conversation
하나의 텔레그램 그룹/토픽/스레드 단위 대화 세션

필드 예시:
- id
- platform (`telegram`)
- chat_id
- topic_id nullable
- title
- status (`active`, `paused`, `done`, `failed`)
- created_at
- updated_at

### participant
대화 참여자 정의

필드 예시:
- id
- conversation_id
- type (`user`, `agent`, `system`)
- handle (`gpt`, `claude`, `copilot`, `manager`)
- display_name
- provider
- model
- is_active

### message
대화 메시지

필드 예시:
- id
- conversation_id
- participant_id
- telegram_message_id nullable
- reply_to_message_id nullable
- raw_text
- rendered_text
- message_type (`user`, `agent`, `status`, `artifact`, `system`)
- created_at

### mention
메시지 내 멘션 추적

필드 예시:
- id
- message_id
- target_participant_id
- mention_text

### agent_run
각 agent 실행 기록

필드 예시:
- id
- conversation_id
- agent_handle
- trigger_message_id
- input_snapshot
- output_snapshot
- provider
- model
- status (`queued`, `running`, `done`, `failed`)
- started_at
- finished_at
- error nullable

---

## 핵심 흐름

### 1. 사용자 입력
사용자가 텔레그램 그룹에서 메시지 입력

예:
- `@manager 사업계획서 초안 만들어`
- `@planner 이 작업 쪼개`
- `@gpt 초안 작성`
- `@claude 비판해`
- `얘들아 이거 토론해서 결론 줘`

### 2. 텔레그램 어댑터 처리
- webhook 또는 polling으로 수신
- chat_id / topic_id / sender / text 파싱
- 멘션 / 명령어 / free-form 요청 분류
- conversation 조회 또는 생성

### 3. 오케스트레이터 판단
- 메시지가 누구를 호출하는지 해석
- 단일 agent 실행인지
- 멀티 agent 토론인지
- artifact 생성 요청인지 분기

### 4. agent 실행
각 agent는 내부 prompt template + 역할 정책으로 동작

예:
- Planner: 작업 분해
- Writer: 초안 생성
- Critic: 허점 지적
- Coder: 코드/설계 제안
- Reviewer: 검수
- Manager: 최종 정리

### 5. 텔레그램 메시지 출력
출력 시 각 agent가 실제 발화하는 것처럼 렌더링

예:
- `[호랑이/GPT] 초안 작성 시작`
- `@haienna_bot 이 초안의 허점을 봐줘`
- `[하이에나/Claude] 시장 근거가 부족함`

### 6. 결과 저장
- message 저장
- agent_run 저장
- artifact job 필요 시 enqueue

---

## 멀티 에이전트 연출 방식

중요:
**텔레그램에서 봇들이 직접 서로 읽고 반응하는 구조에 집착하지 말 것.**
핵심은 **사용자에게 그렇게 보이는 UX**다.

구현 방식:
- 중앙 오케스트레이터가 모든 대화 상태를 알고 있음
- 각 agent의 발화를 순차 생성
- 필요한 경우 메시지 안에 `@agent_handle`를 넣어 연출
- 실제 트리거는 서버 내부 정책으로 처리

즉,
- 겉보기: 에이전트들끼리 협업
- 실제: 중앙 상태기계 기반 turn orchestration

---

## Agent 정책 설계

### Planner
역할:
- 요청 해석
- 작업 분해
- 다음 담당 agent 지정

출력 예:
- 목표
- 입력 자료
- 작업 단계
- 담당자 할당
- 리스크

### Writer
역할:
- 초안 작성
- 설명 문안 생성
- 문서 구조화

### Critic
역할:
- 논리 결함
- 근거 부족
- 빠진 전제
- 과장 표현 검출

### Coder
역할:
- 코드 설계
- 리팩터링 방향
- 구현 TODO
- patch proposal

### Reviewer
역할:
- 산출물 품질 점검
- 요구사항 충족 여부 확인

### Manager
역할:
- 토론 종료 판단
- 최종 결론 정리
- artifact 생성 지시

---

## Provider 매핑 예시

- Planner → GPT or Claude
- Writer → GPT
- Critic → Claude
- Coder → Copilot / Codex / GPT
- Reviewer → Claude
- Manager → GPT

주의:
provider는 agent와 1:1 고정하지 말고 설정 파일로 분리

예:
```yaml
agents:
  planner:
    provider: openai
    model: gpt-5
  critic:
    provider: anthropic
    model: claude-sonnet
  coder:
    provider: copilot
    model: codex
```

---

## Telegram 기능 요구사항

### 필수
- 그룹/슈퍼그룹 지원
- 토픽(thread) 지원 가능하게 설계
- 명령어 지원
- 멘션 파싱
- reply chain 추적
- message edit 무시 또는 제한 지원
- 파일 업로드 수신

### 추천 명령어
- `/agents` : 참여 에이전트 목록
- `/mode` : 실행 모드 변경
- `/run` : 토론/작업 시작
- `/status` : 현재 작업 상태
- `/stop` : 작업 중단
- `/final` : 현재까지 결론 요약
- `/export md`
- `/export docx`
- `/export xlsx`
- `/export pptx`

---

## 실행 모드

### mode=direct
호출한 agent만 실행

### mode=debate
2~N개 agent가 순차 토론 후 manager가 정리

### mode=pipeline
planner → writer → critic → reviewer → manager 순차 처리

### mode=artifact
최종 문서를 바로 생성

---

## 기존 job 시스템 활용안

기존 `jobs`를 버리지 말고 확장한다.

새 job type 제안:
- `conversation_turn`
- `agent_discussion`
- `artifact_export`
- `telegram_dispatch`
- `review_cycle`

기존 artifact 생성 pipeline은 manager 결과물 이후 단계로 재사용

---

## API 확장 포인트

### Telegram inbound
- `POST /telegram/webhook`

### Conversation
- `GET /conversations/{id}`
- `GET /conversations/{id}/messages`
- `POST /conversations/{id}/run`
- `POST /conversations/{id}/stop`

### Agents
- `GET /agents`
- `POST /agents/reload-config`

### Export
- `POST /conversations/{id}/export`

---

## 상태 머신 제안

```text
idle
  → received
  → planned
  → running
  → waiting_review
  → summarizing
  → exporting
  → done

실패 시:
running → failed
exporting → failed
```

각 단계마다 텔레그램에 상태 메시지를 보낼 수 있게 한다.

---

## 텔레그램 메시지 포맷 가이드

### 상태 메시지
- `🟡 Planner가 작업을 분해 중`
- `🟠 Critic이 초안 검토 중`
- `🟢 최종 요약 완료`

### agent 발화 메시지
형식 예:
```text
[호랑이 · Coder]
@haienna_bot 초안 구조는 괜찮은데 시장 데이터 근거가 약해.
나는 기술 구현 파트부터 보강할게.
```

### 최종 메시지
- 핵심 결론
- 다음 액션
- 첨부 artifact 링크/파일

---

## 영속성 / 로그 / 운영

반드시 남길 것:
- 원본 사용자 메시지
- 라우팅 결과
- agent input/output snapshot
- provider/model
- token / latency / cost
- artifact 생성 기록
- 실패 원인

운영용 테이블/로그:
- prompt_logs
- provider_calls
- dead_letters
- replay_requests

---

## 장애 대응

### 실패 유형
- provider timeout
- Telegram 전송 실패
- artifact 생성 실패
- invalid mention
- malformed command
- context overflow

### 대응
- retry 정책 분리
- dead-letter 큐 유지
- `/retry <run_id>` 또는 ops API 제공
- manager가 실패 요약을 사용자에게 알리기

---

## 보안 / 권한

최소 요구사항:
- 허용된 Telegram chat_id만 처리
- 관리자 사용자 ID allowlist
- ops/admin API token 분리
- 파일 업로드 크기 제한
- provider secret 환경변수 관리
- conversation export access control

---

## 설정 파일 예시

```yaml
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  allowed_chat_ids:
    - -1001234567890
  webhook_url: ${TELEGRAM_WEBHOOK_URL}

orchestrator:
  default_mode: pipeline
  max_turns: 6
  auto_summary: true

agents:
  planner:
    enabled: true
    display_name: PM
    provider: openai
    model: gpt-5
  writer:
    enabled: true
    display_name: Writer
    provider: openai
    model: gpt-5
  critic:
    enabled: true
    display_name: Critic
    provider: anthropic
    model: claude-sonnet
  coder:
    enabled: true
    display_name: Coder
    provider: copilot
    model: codex
  reviewer:
    enabled: true
    display_name: Reviewer
    provider: anthropic
    model: claude-sonnet
  manager:
    enabled: true
    display_name: Lead
    provider: openai
    model: gpt-5
```

---

## 개발 순서

### Phase 1
- Telegram webhook 연결
- conversation/message 모델 추가
- direct mode 구현
- 단일 agent 호출 후 응답 전송

### Phase 2
- planner / writer / critic / manager 순차 pipeline
- 상태 메시지 출력
- run 로그 저장

### Phase 3
- 멀티 에이전트 멘션 연출
- debate mode 구현
- artifact export 연결

### Phase 4
- topic/thread 지원
- 파일 업로드 기반 작업
- reviewer / retry / dead-letter 안정화

### Phase 5
- web dashboard 추가
- conversation viewer
- artifact/history viewer
- run replay UI

---

## 우선순위

### 최우선
1. Telegram inbound/outbound
2. conversation/message schema
3. orchestrator engine
4. single-turn agent 실행
5. pipeline mode
6. artifact export 연동

### 후순위
1. 웹 대시보드
2. 고급 권한 체계
3. 대규모 병렬 큐
4. 고급 관찰성 UI

---

## Codex 작업 지시 핵심 요약

아래 목표로 리팩터링 시작:

1. 기존 `docflow-ai` 백엔드는 유지한다.
2. Telegram 기반 conversation/message 계층을 새로 추가한다.
3. 중앙 orchestrator가 여러 agent 역할(planner, writer, critic, coder, reviewer, manager)을 관리하게 만든다.
4. 각 agent 발화는 텔레그램에서 서로 멘션하며 협업하는 것처럼 보이게 렌더링한다.
5. 실제 제어는 중앙 상태기계가 담당한다.
6. 기존 artifact pipeline(md/docx/xlsx/pptx)을 최종 export 단계로 재사용한다.
7. 1차 목표는 web UI가 아니라 Telegram MVP 완성이다.

---

## 절대 하지 말 것

- 기존 코드를 전부 폐기하고 새 프로젝트로 갈아엎기
- 텔레그램 UX를 위해 artifact pipeline을 깨버리기
- agent 개념 없이 provider 호출만 남발하기
- 대화 로그를 비영속으로 처리하기
- “진짜 봇끼리 대화” 구현에 집착해서 구조를 복잡하게 만들기

---

## 최종 판단
이 프로젝트는 **폐기 대상이 아니라 확장 대상**이다.  
단, 이름만 DocFlow AI인 문서 공장에서 멈추지 말고,  
**Telegram-first agent orchestration platform**으로 축을 넓혀야 한다.
