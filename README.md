# 구매 전환 퍼널 분석 대시보드

Streamlit 기반 이벤트 퍼널·카테고리 성과·리텐션 대시보드입니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud 배포

- 저장소 루트의 `app.py`를 앱 파일로 선택합니다.
- 의존성은 `requirements.txt`에서 설치합니다.
- 앱이 사용하는 사전 집계 파일은 `data/processed/`에 포함되어야 합니다.
- 카테고리 결합 이벤트 파일은 Git LFS로 관리합니다.

## 분석 기준

- 관측 기준 시각: 2015-09-15 21:00:00 UTC
- 시간 기준: UTC
- 완전 중복 이벤트 행은 제거한 데이터 사용
- `data/raw/` 원본 파일은 보존하고 분석 결과는 `data/processed/`에 저장
