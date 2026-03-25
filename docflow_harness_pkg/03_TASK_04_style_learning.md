# Task 04 - Style Learning Layer

## 목표
회사 기존 문서를 바탕으로 문체/문단 구조/자주 쓰는 표현을 추출하고, RAG와 분리된 style context로 생성에 주입한다.

## 완료 조건
- style pattern 저장 구조가 존재한다.
- 문서에서 style pattern을 추출하는 파이프라인이 있다.
- section 기준으로 style retrieval이 가능하다.
- writer가 `rag_context + style_context` 를 함께 사용한다.
- UI에서 style mode / strength 선택값을 backend에 전달할 수 있다.

## 최소 구현 범위
- StylePattern 모델
- style_patterns 저장소
- style extraction pipeline
- section별 style retrieval
- prompt injection 규칙
- style mode / strength contract

## 구현 지시
1. style과 factual context를 분리된 구조로 설계한다.
2. style extraction은 초기에는 LLM 기반 batch 추출이어도 된다.
3. section별 패턴 묶음을 저장하고, top-k retrieval이 가능해야 한다.
4. generation 시 section별로 적절한 style을 붙일 수 있게 한다.

## 스타일 모드
- 기본
- 회사 스타일
- 강한 스타일 적용

## 스타일 강도
- 약함
- 중간
- 강함

## 주의사항
- style은 사실 근거를 대체하면 안 된다.
- 강한 스타일도 원문 복제처럼 보이지 않게 해야 한다.
- source_doc 추적 필드는 남긴다.

## 수동 테스트
- style off / on 비교
- 동일 섹션에서 style pattern 반영 확인
- 강도별 출력 차이 확인
- 근거 내용이 style 때문에 왜곡되지 않는지 확인
