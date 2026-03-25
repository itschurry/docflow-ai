# DocFlow AI - Execution Plan

## Codex 작업 순서
1. `00_PROJECT_RULES.md` 읽기
2. `02_SYSTEM_MAP.md` 읽기
3. `03_TASK_01_reviewer_qa_merge.md` 수행
4. `03_TASK_02_rag_integration.md` 수행
5. `03_TASK_03_rag_orchestrator.md` 수행
6. `03_TASK_04_style_learning.md` 수행
7. `03_TASK_05_rag_ui_ux.md` 수행
8. `04_ACCEPTANCE_GLOBAL.md` 기준으로 전체 검증

## Codex 출력 방식
각 task 시작 전 아래만 먼저 출력한다.
- 요구사항 요약
- 수정 대상 파일 예상
- 선행 의존성
- 리스크

그 다음 구현하고, 완료 후 아래를 출력한다.
- 실제 변경 파일
- 구현 요약
- 테스트/검증 결과
- 남은 이슈

## 작업 전략
- reviewer 제거는 선행 작업이다. 이후 문서들은 모두 qa 기반 workflow를 전제로 한다.
- RAG integration은 최소 동작 파이프라인부터 넣고, 고급 저장소는 확장 포인트로 남긴다.
- orchestrator 상태 분기는 retrieval 상태 enum/contract를 먼저 정의한 뒤 연결한다.
- style layer는 writer 우선 연결 후 필요 시 critic/qa 확장 가능 구조로 만든다.
- UI/UX는 backend capability와 반드시 같이 연결한다.
