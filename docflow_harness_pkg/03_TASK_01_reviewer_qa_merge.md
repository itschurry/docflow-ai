# Task 01 - Reviewer 제거 및 QA 통합

## 목표
`reviewer` 역할을 제거하고, 최종 검토/요구사항 검수/품질 보증 책임을 `qa`로 통합한다.

## 완료 조건
- workflow가 planner → writer → critic → qa → manager 로 고정된다.
- config / validator / orchestrator / route / frontend 어디에도 reviewer 의존이 남지 않는다.
- qa prompt가 reviewer 역할까지 흡수한다.
- UI에는 reviewer 대신 qa만 표시된다.

## 수정 범위
- agents config
- suggested_next_agent validation / enum / alias mapping
- orchestrator fixed chain
- review 관련 route / orchestration 분기
- frontend agent label / avatar / 상태 표시
- 테스트 코드 및 fixture

## 구현 지시
1. `reviewer` 문자열 참조를 전역 검색한다.
2. config에서 reviewer 블록 제거 후 qa 설명을 확장한다.
3. next-agent 허용 목록과 fallback 로직을 qa 기준으로 치환한다.
4. review 관련 artifact type이 `review_notes`를 계속 써야 하면 유지하되, 생산 주체는 qa로 바꾼다.
5. frontend에서 reviewer 선택/표시 UI를 제거한다.

## 주의사항
- disabled reviewer 전제 로직이 숨어 있을 수 있다. alias/fallback 분기까지 끝까지 제거한다.
- 기존 저장 데이터나 로그 포맷이 reviewer 문자열을 기대하면 호환성 처리 여부를 명시한다.

## 수동 테스트
- 기본 생성 workflow 1회 실행
- agent step label 확인
- qa가 최종 검토 메모를 남기는지 확인
- reviewer 관련 UI/문구가 남아 있는지 확인
