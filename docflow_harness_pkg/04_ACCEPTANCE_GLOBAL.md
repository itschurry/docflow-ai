# Global Acceptance Checklist

## 워크플로우
- [ ] 기본 체인이 planner → writer → critic → qa → manager 로 동작한다.
- [ ] reviewer 관련 참조가 config/backend/frontend/test에서 제거되었다.
- [ ] orchestrator가 next step 최종 결정을 수행한다.

## RAG
- [ ] 업로드 문서가 인덱싱된다.
- [ ] chunk / metadata / retrieval이 동작한다.
- [ ] source filter가 동작한다.
- [ ] retrieval status가 계산된다.
- [ ] retrieval이 empty/weak일 때 안전한 fallback이 있다.

## Style
- [ ] style pattern 저장 및 retrieval이 가능하다.
- [ ] writer가 rag + style context를 함께 사용한다.
- [ ] style mode / strength가 end-to-end로 연결된다.

## UI
- [ ] Knowledge 메뉴 및 화면이 존재한다.
- [ ] 인덱싱 상태가 사용자에게 보인다.
- [ ] 참고 자료 선택 UI가 동작한다.
- [ ] 결과 화면에서 참고 문서가 보인다.
- [ ] reviewer UI가 제거되고 qa 중심 표시로 정리되었다.

## 품질
- [ ] 주요 변경 파일에 테스트 또는 최소 검증 루틴이 있다.
- [ ] 실패/빈 결과/충돌 상태에 대한 로그 또는 상태 표시가 있다.
- [ ] 관련 없는 대규모 리팩토링 없이 목적 범위 내 구현이다.
