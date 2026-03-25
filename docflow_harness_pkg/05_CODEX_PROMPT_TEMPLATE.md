# Codex Prompt Template

아래 문서들을 작업 기준으로 사용해.

우선순위:
1. `00_PROJECT_RULES.md`
2. `02_SYSTEM_MAP.md`
3. 해당 `03_TASK_xx_*.md`
4. `04_ACCEPTANCE_GLOBAL.md`

작업 방식:
1. 먼저 문서를 읽고 요구사항 요약, 수정 대상 파일 예상, 선행 의존성, 리스크를 정리해.
2. 아직 바로 대규모 수정하지 말고 최소 구현 전략부터 제시해.
3. 그 다음 구현해.
4. 구현 후 acceptance 기준으로 자체 검증해.
5. 마지막에 아래 형식으로 정리해.
   - 변경 파일 목록
   - 핵심 구현 내용
   - 테스트/검증 결과
   - 남은 이슈

추가 규칙:
- reviewer는 제거 대상이며 qa로 통합한다.
- agent의 suggested_next_agent는 참고값일 뿐, orchestrator policy가 우선이다.
- RAG context와 style context는 분리해서 다뤄라.
- UI 변경이 필요한 작업은 backend/state 연결까지 같이 끝내라.
- 관련 없는 리팩토링은 하지 마라.
