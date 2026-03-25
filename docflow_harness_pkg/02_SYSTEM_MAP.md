# DocFlow AI - System Map

## 핵심 도메인
- Agents: planner, writer, critic, qa, manager
- Orchestrator: 단계 진행, handoff, 상태 판단
- Retrieval Layer: chunking, embedding, vector search, context build
- Style Layer: 회사 문체/패턴 추출 및 section 기반 style injection
- Frontend: knowledge library, reference selection, evidence visibility

## 예상 수정 영역
- backend agent config / prompt / identity map
- orchestrator workflow policy / next-agent validation / route handling
- document parsing / indexing / retrieval service
- DB schema / migration / repository layer
- frontend knowledge pages / create form / result panel / agent labels

## 핵심 데이터 객체
- Chunk
- document_chunks row
- retrieval_status: OK | WEAK | EMPTY | CONFLICT
- StylePattern
- style_patterns row
- source_context / rag_context / style_context

## 설계 가드레일
- retrieval context와 style context는 분리된 함수/계층으로 유지
- orchestrator는 suggested_next_agent를 참고만 하고 최종 결정은 policy가 수행
- qa는 reviewer의 최종 검토 책임까지 포함
- 결과 화면에는 참고 문서와 가능한 경우 근거 문단을 표시
