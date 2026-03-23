# DocFlow AI — Patch Notes

---

## v0.3.1 — Multi-Bot Identity Layer + Polling Fixes (2026-03-23)

### 개요
Phase 1.5: 각 에이전트 역할별 별도 Telegram 봇 계정으로 메시지 발송.
모든 봇이 동시에 인바운드 폴링 수행. 무한루프 방지 필터 추가.

### 추가
- **BotRegistry** (`adapters/telegram/registry.py`): agents.yaml의 `telegram.bots` 섹션에서 봇 토큰/유저명 로드, `${ENV_VAR}` 치환
- **MultiBotOutbound** (`adapters/telegram/outbound.py`): identity별 봇 계정으로 `sendMessage` 발송
- **BotDispatcher** (`adapters/telegram/dispatcher.py`): role→identity 해석, @멘션 자동삽입, reply chain 관리
- **멀티봇 폴링** (`scripts/polling.py`): 4개 봇 동시 `getUpdates` 폴링, 401 즉시 중단

### 수정
- **무한루프 차단** (`handlers.py`): `is_bot=True` 메시지 무시 — 봇 발신 메시지가 다시 파이프라인을 트리거하는 문제 해결
- **DB 스키마** (`conversation_models.py`): `conversations.last_message_ids`, `messages.speaker_*`, `agent_runs.output_message_id` 컬럼 추가
- **OrchestratorEngine**: dispatcher 사용, reply chain 추적, speaker identity DB 저장
- **.env 파싱**: 인라인 주석(`# ...`) 제거로 토큰 파싱 오류 해결
- **agents.yaml**: 전 에이전트 gpt-4o 통일 (Anthropic 비활성화)

---

## v0.3.0 — Telegram Multi-Agent Platform (2026-03-23)

### 개요

기존 문서 생성 백엔드(`DocFlow AI v0.2.x`)를 유지하면서,  
**Telegram 기반 멀티 에이전트 오케스트레이션 플랫폼**으로 확장한 Phase 1 패치.

핵심 목표:
- 텔레그램 그룹에서 AI 에이전트들이 협업하는 것처럼 보이는 UX
- 실제 제어는 중앙 오케스트레이터(상태 머신 기반)가 담당
- 기존 artifact pipeline(md/docx/xlsx/pptx)은 무변경 유지

---

### 신규 파일

#### `apps/api/config/agents.yaml`
에이전트 설정 파일. 각 에이전트의 provider/model/system_prompt를 코드 없이 변경 가능.

```yaml
agents:
  planner:   { provider: openai,     model: gpt-4.1-mini }
  writer:    { provider: openai,     model: gpt-4.1-mini }
  critic:    { provider: anthropic,  model: claude-3-5-haiku-latest }
  coder:     { provider: openai,     model: gpt-4.1-mini }
  reviewer:  { provider: anthropic,  model: claude-3-5-haiku-latest }
  manager:   { provider: openai,     model: gpt-4.1-mini }
```

---

#### DB 모델 — `apps/api/app/conversation_models.py`

5개 신규 테이블 추가. 기존 테이블 무변경.

| 테이블 | 설명 |
|--------|------|
| `conversations` | 텔레그램 그룹/토픽 단위 대화 세션 |
| `participants` | 대화 참여자 (user / agent / system) |
| `conv_messages` | 대화 메시지 전체 로그 |
| `mentions` | 메시지 내 `@handle` 멘션 추적 |
| `agent_runs` | 에이전트 실행 기록 (input/output snapshot, 상태, 시간) |

**대화 상태 흐름:**
```
idle → received → running → summarizing → done
                          ↘ failed
```

---

#### Alembic 마이그레이션 — `migrations/versions/20260323_0003_conversation_tables.py`

리비전: `20260323_0003` (부모: `20260319_0002`)  
5개 테이블 + 4개 인덱스 생성.

```bash
# 적용
alembic upgrade head
# 롤백
alembic downgrade 20260319_0002
```

---

#### Conversation 서비스 — `apps/api/app/conversations/`

| 파일 | 역할 |
|------|------|
| `service.py` | Conversation/Participant/Message/AgentRun CRUD |
| `selectors.py` | 컨텍스트 프롬프트 빌드, 최근 에이전트 출력 조회 |
| `serializer.py` | DB 모델 → JSON dict 직렬화 |

주요 메서드:
- `get_or_create_conversation(chat_id, topic_id)` — 진행 중인 대화 재사용 또는 신규 생성
- `create_agent_run` / `start_agent_run` / `finish_agent_run` — 실행 생명주기 관리
- `build_context_prompt(db, conversation_id)` — 대화 이력을 LLM 프롬프트용 텍스트로 변환

---

#### 에이전트 시스템 — `apps/api/app/agents/`

| 파일 | 역할 |
|------|------|
| `base.py` | `BaseAgent` 추상 클래스, `AgentConfig`, `AgentResult`, `build_provider()` |
| `registry.py` | `agents.yaml` 파싱 → 에이전트 인스턴스 딕셔너리 반환 |
| `planner.py` | 작업 분해, 담당자 지정, 리스크 분석 |
| `writer.py` | 초안 문서 작성 |
| `critic.py` | 논리 결함, 근거 부족, 과장 표현 검출 |
| `coder.py` | 기술 구현 방향, 코드 구조, TODO |
| `reviewer.py` | 요구사항 충족 여부 검수, 품질 점수 |
| `manager.py` | 최종 결론 정리, 다음 액션 지시 |

**에이전트 실행 흐름:**
```
BaseAgent.run(user_request, context)
  → build_prompt()          # 에이전트별 프롬프트 구성
  → LLMProvider.generate_text()   # OpenAI / Anthropic / Stub
  → AgentResult(text, provider, model)
```

---

#### 오케스트레이터 — `apps/api/app/orchestrator/`

| 파일 | 역할 |
|------|------|
| `state_machine.py` | `ConversationStatus` StrEnum + 허용 전환 테이블 |
| `policies.py` | 모드별 파이프라인 정의, 멘션 감지, 명령어 파싱 |
| `router.py` | `RoutingDecision` — 메시지에서 mode/pipeline/direct_handle 결정 |
| `engine.py` | **중앙 오케스트레이터** 싱글톤 — 에이전트 파이프라인 실행, DB 로깅, Telegram 전송 |

**실행 모드:**

| 모드 | 파이프라인 | 트리거 |
|------|-----------|--------|
| `pipeline` | planner → writer → critic → reviewer → manager | 기본 모드 |
| `debate` | planner → writer → critic → manager | `/mode debate` |
| `artifact` | planner → writer → manager | `/mode artifact` |
| `direct` | 멘션된 에이전트 1개만 | `@handle 메시지` |

`OrchestratorEngine.process_message()` 핵심 처리 순서:
1. 대화 세션 조회/생성
2. 사용자 메시지 DB 저장
3. `router.route()` — 실행 모드 및 파이프라인 결정
4. 파이프라인 순서대로 에이전트 실행 (각 단계마다 이전 출력을 컨텍스트로 전달)
5. 각 에이전트 발화를 Telegram으로 전송
6. `agent_runs` 테이블에 input/output snapshot, 시간, 오류 기록
7. 대화 상태 `done` → `idle` 전환

---

#### Telegram 어댑터 — `apps/api/app/adapters/telegram/`

| 파일 | 역할 |
|------|------|
| `bot.py` | `httpx.AsyncClient` 기반 Telegram Bot API 클라이언트 |
| `formatter.py` | HTML 형식 메시지 렌더링 (에이전트 발화, 상태, 도움말 등) |
| `commands.py` | `/agents` `/mode` `/status` `/stop` `/final` `/export` 핸들러 |
| `handlers.py` | Webhook update 파싱 → 명령어/메시지 분기 → orchestrator 호출 |

**지원 명령어:**
```
/agents          에이전트 목록 및 provider/model 정보
/mode <mode>     실행 모드 변경 (pipeline|debate|artifact|direct)
/status          현재 대화 상태 조회
/stop            진행 중 작업 중단
/final           가장 최근 manager 결론 출력
/export <fmt>    결과 내보내기 (md|docx|xlsx|pptx) — Phase 3 연동 예정
/help            도움말
```

**에이전트 발화 포맷 (Telegram HTML):**
```
[📋 PM]
목표: 사업계획서 초안 작성
작업단계: 1. 시장 분석 → 2. 초안 작성 → 3. 검토
...
```

---

### 수정된 파일

#### `apps/api/requirements.txt`
```diff
+ pyyaml>=6.0.2
+ httpx>=0.27.0
```

#### `apps/api/.env.example`
```diff
+ TELEGRAM_BOT_TOKEN=
+ TELEGRAM_ALLOWED_CHAT_IDS=
+ TELEGRAM_WEBHOOK_URL=
+ TELEGRAM_WEBHOOK_SECRET=
+ AGENT_CONFIG_PATH=./config/agents.yaml
+ ORCHESTRATOR_DEFAULT_MODE=pipeline
+ ORCHESTRATOR_MAX_TURNS=6
+ ORCHESTRATOR_AUTO_SUMMARY=true
```

#### `apps/api/app/core/config.py`
신규 설정 항목 추가 (기존 설정 무변경):

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `telegram_bot_token` | `""` | BotFather에서 발급한 토큰 |
| `telegram_allowed_chat_ids` | `[]` | 허용 chat_id 목록. 비어 있으면 전체 허용 (개발 모드) |
| `telegram_webhook_url` | `""` | Telegram이 업데이트를 보낼 공개 URL |
| `telegram_webhook_secret` | `""` | 웹훅 요청 검증용 시크릿 |
| `agent_config_path` | `./config/agents.yaml` | 에이전트 설정 파일 경로 |
| `orchestrator_default_mode` | `pipeline` | 기본 실행 모드 |
| `orchestrator_max_turns` | `6` | 최대 에이전트 턴 수 |
| `orchestrator_auto_summary` | `true` | 파이프라인 완료 후 자동 요약 여부 |

#### `apps/api/app/api/routes.py`
신규 엔드포인트 8개 추가:

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/telegram/webhook` | Telegram 웹훅 수신 (백그라운드 처리) |
| `POST` | `/telegram/setup-webhook` | 웹훅 URL Telegram에 등록 |
| `GET` | `/conversations/{id}` | 대화 세션 조회 |
| `GET` | `/conversations/{id}/messages` | 메시지 목록 (`?limit=50`) |
| `GET` | `/conversations/{id}/runs` | 에이전트 실행 로그 |
| `POST` | `/conversations/{id}/stop` | 대화 강제 종료 |
| `GET` | `/agents` | 등록된 에이전트 목록 |
| `POST` | `/agents/reload-config` | `agents.yaml` hot-reload |

#### `apps/api/app/main.py`
```diff
+ from app import conversation_models  # noqa: F401
```
`auto_create_tables=true` 시 신규 테이블 자동 생성.

---

### 배포 가이드

#### 1. 의존성 설치
```bash
cd apps/api
pip install -r requirements.txt
```

#### 2. 환경변수 설정
```bash
cp .env.example .env
# .env 파일 편집
TELEGRAM_BOT_TOKEN=1234567890:AAFxxxxx
TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890
TELEGRAM_WEBHOOK_URL=https://yourdomain.com/telegram/webhook
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

#### 3. DB 마이그레이션
```bash
alembic upgrade head
```

#### 4. 서버 실행
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### 5. 웹훅 등록 (최초 1회)
```bash
curl -X POST https://yourdomain.com/telegram/setup-webhook
```

---

### 알려진 제한사항 (Phase 2~3에서 해결 예정)

| 항목 | 현재 상태 | 계획 |
|------|-----------|------|
| `/export` 명령어 | 안내 메시지만 출력 | Phase 3 — artifact pipeline 연동 |
| Telegram 파일 업로드 수신 | 미구현 | Phase 4 |
| topic/thread 별 독립 대화 | chat_id 기반 단일 대화만 지원 | Phase 2 |
| debate 모드 에이전트 간 멘션 연출 | 기본 순차 출력 | Phase 3 |
| `/retry <run_id>` | 미구현 | Phase 2 |

---

### 테스트

```bash
cd apps/api
pytest tests/ -q
# 15 passed ✅ (기존 테스트 전량 통과, 회귀 없음)
```

---

### 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| v0.3.0 | 2026-03-23 | Telegram 멀티 에이전트 플랫폼 Phase 1 |
| v0.2.0 | 2026-03-19 | Ops API Key, Dead-letter 큐 |
| v0.1.0 | 2026-03-19 | 초기 문서 생성 파이프라인 |
