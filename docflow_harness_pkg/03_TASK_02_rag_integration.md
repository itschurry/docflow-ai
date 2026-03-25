# Task 02 - RAG Integration 기본 구축

## 목표
파일 요약 기반 문서 생성에서 실제 근거 검색 기반 문서 생성으로 전환할 최소 RAG 파이프라인을 구축한다.

## 완료 조건
- 업로드 파일이 chunk → embedding → vector/index 저장 흐름을 가진다.
- task 실행 시 `build_rag_context(query, source_file_ids, top_k)` 형태로 근거 context를 만들 수 있다.
- retrieved context는 source metadata와 함께 prompt에 주입된다.
- source file filter가 동작한다.
- retrieval 결과가 없거나 약할 때 호출부가 이를 식별할 수 있다.

## 최소 구현 범위
- Chunk 모델
- chunking service
- embedding service adapter
- vector/index 저장 abstraction
- retriever
- build_rag_context
- document_chunks 저장소 및 migration
- task route/orchestration 연결

## 추천 구현 순서
1. chunk metadata schema 정의
2. indexing 파이프라인 연결
3. vector store abstraction 구현
4. retriever/search 구현
5. rag context formatter 구현
6. 기존 `source_context` 연결부를 교체 또는 래핑

## 메타데이터 필수 항목
- file_id
- file_name
- document_type
- section
- page 또는 logical position
- chunk_index

## 프롬프트 규칙
RAG context는 아래 형태를 유지한다.
- source 식별 가능
- chunk 내용 표시
- 모델이 근거를 참조할 수 있는 구조

## 주의사항
- 초기 저장소는 SQLite + FAISS 같은 단순 조합이어도 되지만, interface는 교체 가능해야 한다.
- parsing/chunking 실패가 전체 업로드를 막지 않도록 상태를 분리한다.
- xlsx/pptx/pdf/docx별 chunk 기준은 adapter로 분리 가능하게 만든다.

## 수동 테스트
- 파일 업로드 후 인덱싱 상태 확인
- 특정 요청으로 top-k retrieval 확인
- 선택한 source_file_ids 외 문서가 섞이지 않는지 확인
- 결과가 empty일 때 downstream이 안전하게 동작하는지 확인
