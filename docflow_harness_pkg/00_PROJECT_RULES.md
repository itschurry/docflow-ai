# DocFlow AI - Codex Harness Rules

## 목적
이 문서 묶음은 DocFlow AI의 RAG / 스타일 학습 / 오케스트레이션 / QA 통합 / UI 연동 작업을 Codex가 안정적으로 수행하도록 하기 위한 작업 하네스다.

## 작업 원칙
- 구현은 **작은 단위 커밋 가능한 변경**으로 나눈다.
- 아키텍처 개편이 필요하더라도 **기존 동작을 깨지 않는 점진적 변경**을 우선한다.
- 문서 생성 품질 향상 기능은 반드시 **근거 추적 가능성**을 유지해야 한다.
- agent의 자유 handoff보다 **orchestrator policy 중심 제어**를 우선한다.
- reviewer 역할은 제거하고, 검수 책임은 qa에 통합한다.
- 업로드와 참고 범위 선택은 분리한다.
- 스타일은 내용(RAG)과 분리된 별도 계층으로 다룬다.

## 절대 금지
- 관련 없는 대규모 리팩토링 금지
- 기존 API/응답 포맷을 이유 없이 깨는 변경 금지
- UI만 바꾸고 backend/state를 맞추지 않는 반쪽 구현 금지
- backend만 바꾸고 UI에 상태/근거 표시를 생략하는 구현 금지
- retrieval 결과를 근거처럼 보이게 하면서 실제 출처 연결이 없는 구현 금지
- reviewer 제거 작업 중 legacy 문자열/enum/validation 참조를 일부 남겨두는 상태 금지

## 구현 우선순위
1. reviewer → qa 통합
2. RAG integration 기본 파이프라인
3. orchestrator의 retrieval 상태 기반 handoff
4. style learning layer
5. knowledge/RAG UI·UX

## 공통 산출물 규칙
각 작업 완료 시 아래를 남긴다.
- 변경 파일 목록
- 핵심 설계 결정
- 호환성 영향
- 남은 리스크
- 수동 테스트 방법

## 공통 검증 규칙
- lint / type / test 중 가능한 범위를 수행한다.
- 기존 workflow: planner → writer → critic → qa → manager 가 유지되는지 확인한다.
- retrieval이 약하거나 비어 있을 때 fallback 흐름이 깨지지 않는지 확인한다.
- UI에는 사용자가 체감할 수 있는 상태 표시를 반드시 넣는다.
