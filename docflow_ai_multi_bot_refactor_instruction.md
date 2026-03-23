# DocFlow AI 멀티-봇 전환 수정 지시서

## 목적
현재 Phase 1 구현은 **하나의 텔레그램 봇 내부에서 여러 agent role(PM, Writer, Critic, Coder 등)을 순차 실행**하는 구조다.  
이 방식은 기능상 멀티 에이전트 오케스트레이션은 맞지만, 목표 UX인 **“여러 텔레그램 봇이 같은 그룹방에서 서로 멘션하며 협업하는 구조”**와 다르다.

이번 수정의 목표는 기존 orchestrator / conversation / artifact 구조는 유지하면서,  
**single-bot output 구조를 multi-bot identity 구조로 전환**하는 것이다.

---

## 최종 목표 UX

텔레그램 그룹방 안에 아래와 같은 별도 봇 계정들이 존재해야 한다.

- PM 봇
- Writer 봇
- Critic 봇
- Coder 봇
- Reviewer 봇 (선택)
- Manager 봇 (선택, 또는 PM이 겸임)

사용자는 PM 봇 또는 그룹 전체에 작업 지시를 내린다.  
이후 각 봇은 같은 방에서 실제로 자기 계정으로 메시지를 보내며, 서로를 멘션하고 작업을 이어간다.

예시 흐름:

1. 사용자: `@pm_bot 이 주제로 사업계획서 초안 만들어줘`
2. PM 봇: `요청을 작업 단위로 분해하겠습니다. @writer_bot 초안 작성 시작해줘`
3. Writer 봇: `초안 작성 완료. @critic_bot 논리와 빠진 부분 검토 부탁`
4. Critic 봇: `시장 근거와 수치가 부족함. @coder_bot 기술 아키텍처 보강 제안 부탁`
5. Coder 봇: `기술 섹션 보강안 작성 완료. @pm_bot 최종 정리 가능`
6. PM 봇: `최종 요약 및 export 준비 완료`

즉, **역할 분리만이 아니라 발화 주체가 서로 다른 봇 계정으로 보여야 한다.**

---

## 중요한 전제

### 유지할 것
- 기존 FastAPI / DB / jobs / artifacts / providers / orchestrator core
- conversation, message, agent_run 같은 상태 관리 구조
- artifact export pipeline
- agent role 개념 자체

### 바꿔야 할 것
- Telegram outbound를 단일 봇 1개 기준으로 보내는 구조
- agent role과 telegram identity가 결합된 구조
- 최종 출력을 한 봇이 전부 대신 말하는 방식

---

## 핵심 설계 변경

## 1. agent role 과 telegram bot identity 분리

현재 구조는 아마 대략 아래와 비슷할 가능성이 높다:

- planner role
- writer role
- critic role
- coder role

그리고 이 role들의 출력이 모두 **같은 telegram bot token** 으로 전송된다.

이 구조를 아래처럼 분리해야 한다:

- `agent_role`: planner / writer / critic / coder / reviewer / manager
- `bot_identity`: pm / writer / critic / coder / reviewer / manager

즉,
- **role** = 내부 작업 책임
- **identity** = 외부에서 보이는 텔레그램 발화 주체

둘은 분리되어야 하며, 1:1일 수도 있고 나중에 1:N 또는 N:1 매핑도 가능하게 설계한다.

예:
- planner → pm_bot
- manager → pm_bot
- writer → writer_bot
- critic → critic_bot
- coder → coder_bot

---

## 2. Telegram 멀티-봇 송신 계층 추가

현재 outbound adapter가 단일 토큰 기준으로 되어 있다면, 이를 아래 구조로 바꾼다.

필수 요구사항:
- 여러 bot token 로드
- role별 발화 시 어떤 bot identity를 쓸지 결정
- 지정된 bot token으로 메시지 전송
- 동일 conversation 안에서 여러 bot이 번갈아 메시지 가능

예시 설정:

```yaml
telegram:
  bots:
    pm:
      token: ${TELEGRAM_PM_BOT_TOKEN}
      username: pm_bot
      display_name: PM
    writer:
      token: ${TELEGRAM_WRITER_BOT_TOKEN}
      username: writer_bot
      display_name: Writer
    critic:
      token: ${TELEGRAM_CRITIC_BOT_TOKEN}
      username: critic_bot
      display_name: Critic
    coder:
      token: ${TELEGRAM_CODER_BOT_TOKEN}
      username: coder_bot
      display_name: Coder
```

필수 구현 함수 예시:

- `send_message_as(identity, chat_id, text, reply_to_message_id=None)`
- `resolve_identity_for_role(role) -> identity`
- `send_agent_turn(conversation_id, role, content, reply_to=None)`

주의:
이 계층은 **role 이름만 바꾸는 formatter가 아니라**, 실제로 **각 bot token으로 발화**해야 한다.

---

## 3. speaker scheduler / turn controller 추가

기존 pipeline은 내부적으로 planner → writer → critic → manager 순서로 흘러갔을 수 있다.  
하지만 이제는 **누가 다음에 말할지**가 명시적으로 상태로 관리되어야 한다.

필수 추가 개념:
- `next_speaker_role`
- `next_speaker_identity`
- `turn_index`
- `reply_chain_head_message_id`
- `last_message_id_by_identity`

가능하면 아래 형태의 테이블 또는 상태 객체를 둔다:

```text
conversation_runtime_state
- conversation_id
- mode
- current_role
- current_identity
- next_role
- next_identity
- turn_index
- status
```

동작 방식:
1. 사용자 메시지 수신
2. PM role이 첫 판단
3. scheduler가 다음 발화자를 writer로 결정
4. writer identity로 메시지 전송
5. critic identity로 메시지 전송
6. 마지막에 pm identity로 종합

즉, 내부 실행 로직과 외부 발화 순서를 명시적으로 스케줄링해야 한다.

---

## 4. 멘션 기반 렌더링 추가

네가 원하는 UX의 핵심은 “그냥 번갈아 말함”이 아니라  
**서로 멘션하고 이어받는 느낌**이다.

따라서 각 agent output 템플릿 또는 post-processor에서 다음을 지원해야 한다:

- 다음 대상 bot username 자동 삽입
- 필요 시 `reply_to_message_id` 연결
- 같은 토픽 안에서 대화가 이어지도록 메시지 체인 유지

예시:

PM 봇 메시지:
```text
작업을 3단계로 나눴어.
1) 초안 작성
2) 논리 검토
3) 기술 구현 보강

@writer_bot 먼저 초안 작성 시작해줘.
```

Writer 봇 메시지:
```text
초안 1차 작성 완료했어.

@critic_bot 논리/구조/빠진 내용을 검토해줘.
```

Critic 봇 메시지:
```text
문제 정의는 괜찮은데 시장 근거와 수치가 약해.

@coder_bot 기술 구조 보강 아이디어를 제안해줘.
```

중요:
실제 트리거는 내부 orchestrator가 결정해도 된다.  
멘션은 **UX 연출**로 들어가면 충분하다.

---

## 5. inbound 처리 방식 재정의

현재 inbound가 아마 “우리 봇 하나에게 들어온 메시지” 기준일 가능성이 크다.  
멀티-봇 구조로 바꾸면 아래를 고려해야 한다.

### 요구사항
- 그룹방 내 사용자 메시지 수신
- 특정 봇 멘션 파싱
- free-form 요청도 PM 봇이 대표로 받게 가능
- 봇이 보낸 메시지는 상태 기록용으로만 저장 가능
- 실제 다음 턴 실행 트리거는 중앙 서버가 담당

### 핵심 포인트
멀티-봇 UX를 만들더라도,  
**전체 conversation state의 source of truth는 중앙 서버**여야 한다.

즉:
- 봇끼리 진짜 자율로 떠드는 구조에 집착하지 말 것
- 중앙 오케스트레이터가 모든 턴을 결정하고
- 각 봇은 그 결정을 자기 계정으로 발화하는 구조로 유지할 것

---

## 6. 데이터 모델 보강

기존 conversation / message / agent_run 구조를 유지하되 아래 필드를 추가한다.

### message
추가 필드 예시:
- `speaker_role`
- `speaker_identity`
- `speaker_bot_username`
- `telegram_message_id`
- `reply_to_telegram_message_id`
- `is_agent_message`

### participant
추가 필드 예시:
- `participant_type` (`user`, `agent`, `system`)
- `role`
- `identity`
- `telegram_username`
- `telegram_bot_token_key`

### agent_run
추가 필드 예시:
- `speaker_identity`
- `scheduled_by_role`
- `triggered_by_message_id`
- `output_message_id`

핵심은 **role과 identity를 구분해 저장**하는 것.

---

## 7. Telegram dispatcher 모듈 분리

새 모듈을 별도로 만들 것.

추천 파일 구조:

```text
adapters/
  telegram/
    inbound.py
    outbound.py
    registry.py
    formatter.py
    dispatcher.py
```

설명:
- `registry.py` : identity ↔ token / username / display_name 관리
- `outbound.py` : 실제 텔레그램 API 호출
- `formatter.py` : 멘션 포함 메시지 포맷 생성
- `dispatcher.py` : role 기반으로 어떤 identity로 발화할지 결정

핵심 함수 예시:
- `get_bot_client(identity)`
- `dispatch_agent_message(conversation, role, text, next_role=None, reply_to=None)`
- `build_agent_message(role, body, next_identity=None)`

---

## 8. PM/Writer/Critic/Coder 4봇 기준으로 먼저 고정 구현

처음부터 너무 일반화하지 말고,  
우선 아래 4개 봇이 같은 그룹에서 동작하는 MVP부터 완성한다.

1. PM bot
2. Writer bot
3. Critic bot
4. Coder bot

초기 역할:
- PM: 작업 분해 / 시작 / 최종 종합
- Writer: 초안 작성
- Critic: 논리 / 구조 / 보완점 지적
- Coder: 기술 구현 / 설계 제안

Reviewer / Manager는 나중에 추가 가능.  
초반에는 **PM이 manager 역할을 겸임**해도 된다.

---

## 9. reply chain 유지

각 봇이 제멋대로 새 메시지를 던지기만 하면 대화가 산만해진다.  
따라서 기본 규칙을 둔다.

규칙:
- 첫 사용자 메시지를 conversation anchor로 저장
- PM의 첫 응답은 anchor에 reply
- Writer는 PM 메시지에 reply
- Critic은 Writer 메시지에 reply
- Coder는 Critic 메시지에 reply
- PM 최종 요약은 Coder 메시지 또는 anchor에 reply

즉, 텔레그램 상에서도 **실제 대화가 이어지는 것처럼 보이게** 한다.

---

## 10. 실행 모드 단순화

초기 멀티-봇 전환에서는 모드를 단순하게 유지한다.

지원 우선순위:
1. `pipeline`
2. `direct`

### pipeline
PM → Writer → Critic → Coder → PM

### direct
사용자가 특정 봇을 직접 멘션하면 해당 role만 단일 실행

예:
- `@writer_bot 이 문단 초안 작성`
- `@critic_bot 이 문장 비판해`
- `@coder_bot API 구조 제안해`

debate mode 같은 고급 모드는 멀티-봇 전환 안정화 후 추가

---

## 11. artifact export 연결 방식

기존 export pipeline은 유지한다.  
다만 export 트리거는 PM bot 또는 manager role이 담당한다.

예:
- PM bot이 최종 정리 후 `/export docx`
- 또는 사용자 메시지에서 `docx로 뽑아줘` 요청
- manager/pm가 export job enqueue
- 결과 파일을 PM bot 명의로 회신

즉:
- **토론/초안/검토는 여러 봇**
- **최종 산출물 회신은 PM 봇 중심**

---

## 12. 설정 파일 구조 변경

예시:

```yaml
telegram:
  default_chat_mode: pipeline
  allowed_chat_ids:
    - -1001234567890

  bots:
    pm:
      token: ${TELEGRAM_PM_BOT_TOKEN}
      username: pm_bot
      display_name: PM
    writer:
      token: ${TELEGRAM_WRITER_BOT_TOKEN}
      username: writer_bot
      display_name: Writer
    critic:
      token: ${TELEGRAM_CRITIC_BOT_TOKEN}
      username: critic_bot
      display_name: Critic
    coder:
      token: ${TELEGRAM_CODER_BOT_TOKEN}
      username: coder_bot
      display_name: Coder

agents:
  planner:
    provider: openai
    model: gpt-5
    identity: pm
  writer:
    provider: openai
    model: gpt-5
    identity: writer
  critic:
    provider: anthropic
    model: claude-sonnet
    identity: critic
  coder:
    provider: openai
    model: gpt-5
    identity: coder
  manager:
    provider: openai
    model: gpt-5
    identity: pm
```

중요:
identity는 설정으로 분리하고 하드코딩 최소화.

---

## 13. 단계별 구현 순서

## Phase 1.5
목표: 기존 single-bot 구조 위에 multi-bot outbound layer 추가

할 일:
1. 여러 bot token 설정 로드
2. bot registry 구현
3. role → identity 매핑 구현
4. `send_message_as(identity, ...)` 구현
5. PM / Writer / Critic / Coder 4개 봇으로 그룹 발화 테스트

완료 기준:
- 같은 그룹방에서 4개 봇이 각자 메시지를 보낼 수 있음
- PM → Writer → Critic → Coder → PM 흐름이 눈으로 확인됨

---

## Phase 1.6
목표: 멘션/리플라이 UX 보강

할 일:
1. 메시지 템플릿에 다음 봇 멘션 자동 삽입
2. reply-to 체인 유지
3. turn scheduler 고도화
4. 상태 메시지 최소화
5. agent별 프로필/표시명 정리

완료 기준:
- 사용자가 보기엔 실제로 여러 봇이 협업하는 느낌이 남
- 각 발화가 누가 누구에게 넘기는지 분명히 보임

---

## Phase 1.7
목표: direct mode + export 연결

할 일:
1. 특정 봇 직접 호출 지원
2. PM 최종 종합 메시지 구현
3. export job 연결
4. 결과 파일 회신

완료 기준:
- 그룹방에서 직접 호출 / 파이프라인 / export 동작 가능

---

## 14. 구현 시 주의사항

### 절대 하지 말 것
- 기존 orchestrator를 버리고 새 프로젝트로 다시 짜기
- “봇끼리 직접 자율 대화”에 집착해서 구조를 망치기
- 역할(role)과 identity를 다시 섞어버리기
- 메시지 저장 없이 화면 출력만 맞추기
- 초기부터 너무 일반화해서 설정만 복잡하게 만들기

### 반드시 할 것
- source of truth는 중앙 서버로 유지
- Telegram에서는 발화 identity만 분리
- role / identity / provider / output_message_id 를 모두 저장
- reply chain 유지
- PM 중심 진입 UX 유지

---

## 15. 최종 작업 지시 요약

아래 방향으로 리팩터링할 것:

1. 현재 single-bot multi-role 구조를 유지하되, outbound 계층을 multi-bot identity 구조로 바꾼다.
2. PM / Writer / Critic / Coder 각각의 별도 Telegram bot token을 사용한다.
3. 중앙 orchestrator는 계속 전체 conversation state와 turn scheduling을 관리한다.
4. 각 turn의 출력은 해당 role에 매핑된 bot identity로 전송한다.
5. 메시지에는 다음 대상 bot 멘션을 포함해 실제 협업처럼 보이게 만든다.
6. reply-to 체인을 유지해 텔레그램에서 자연스러운 대화 흐름을 만든다.
7. 우선 4봇(PM/Writer/Critic/Coder) 기준 MVP를 완성한 뒤, Reviewer/Manager를 확장한다.
8. 기존 artifact export pipeline은 유지하고 PM bot이 최종 export를 담당하게 한다.

---

## 최종 판단
지금 구현은 실패가 아니다.  
**오케스트레이터 코어는 이미 살아 있고, 부족한 건 “발화 identity 계층” 뿐이다.**  
따라서 이번 수정은 전면 재작성 프로젝트가 아니라,  
**single-bot orchestrator 위에 multi-bot Telegram identity layer를 얹는 리팩터링**으로 진행한다.

