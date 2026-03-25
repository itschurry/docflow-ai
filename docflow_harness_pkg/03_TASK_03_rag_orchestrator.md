# Task 03 - Retrieval 상태 기반 Orchestrator 제어

## 목표
agent가 다음 단계를 임의 결정하는 구조를 줄이고, orchestrator가 retrieval 상태를 반영해 handoff를 결정하도록 변경한다.

## 완료 조건
- orchestrator가 최종 next step을 결정한다.
- retrieval_status `OK | WEAK | EMPTY | CONFLICT` 가 정의되고 workflow 판단에 사용된다.
- 기본 체인 planner → writer → critic → qa → manager 가 유지된다.
- retrieval이 약하거나 비어 있으면 planner 복귀 또는 보정 단계가 동작한다.
- 근거 충돌 시 critic/planner 재정리 흐름이 존재한다.

## 구현 지시
1. retrieval status enum/contract 추가
2. build_rag_context 또는 retrieval 결과에서 status 산출
3. orchestrator policy layer에서 next-agent 결정을 담당
4. agent의 `suggested_next_agent`는 참고값으로만 사용
5. status 기반 fallback/retry path 추가

## 정책 예시
- OK: 정상 체인 진행
- WEAK: planner 재정의 또는 writer 제한 생성
- EMPTY: planner 복귀 후 source selection/질문 정제
- CONFLICT: critic 또는 planner가 근거 충돌 정리

## 주의사항
- 무한 루프 방지용 최대 재시도/재계획 제한 필요
- retrieval 문제와 모델 실패를 구분해 로깅한다.
- 상태값은 frontend나 activity log에 노출 가능한 구조면 더 좋다.

## 수동 테스트
- 충분한 검색 결과 상황
- 약한 검색 결과 상황
- 결과 없음 상황
- 서로 다른 근거 충돌 상황
