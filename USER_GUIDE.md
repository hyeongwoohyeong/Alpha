# Alpha — 사용자 매뉴얼

> Alpha 는 매일 미국주식 유니버스 42 종목을 자동으로 점검해 한국어 리서치 메모를 만들어주는 엔진이다.
> 이 문서는 형우(엔진 소유자) 가 매일 / 매주 어떤 흐름으로 Alpha 를 활용하면 좋은지 정리한 가이드다.

---

## 1. Alpha 가 무엇인지 한 줄로

매일 새벽에 자동으로 41 종목의 시세·재무·뉴스를 끌어와 6 요소로 점수를 매기고, 회사 타입과 Action Tag 를 붙이고, 영어 기사들을 한국어 리서치 메모로 압축해 보여주는 **개인 리서치 어시스턴트** 다.

핵심 목표는 두 가지다.
- 1) **영어 기사를 직접 읽지 않아도 핵심을 이해**하게 한다.
- 2) **무엇을 더 봐야 하는지(Follow-up)** 를 매일 자동으로 큐에 쌓아준다.

매일 매도/매수 신호를 주는 도구가 아니다. **무엇을 우선 살펴볼지 정렬해주는 사전 스크리너**에 가깝다.

---

## 2. 시스템 구조 (한 페이지 요약)

```
[GitHub Actions cron]   →   run_research.py   →   alpha.db   →   app.py (Streamlit Cloud)
   매일 자동 실행              12 단계 파이프라인        SQLite DB         웹 UI / 모바일
```

- **run_research.py**: 데이터 fetch → 분석 → 한국어 요약 → DB 적재 (이 단계가 모든 무거운 일을 한다)
- **alpha.db**: 10 개 테이블 (universe / runs / price_snapshot / news_raw / events / scores / stock_research / daily_brief / decision_log / performance_tracking)
- **app.py**: DB 만 읽어 화면에 보여주는 뷰. 직접 분석을 다시 하지 않는다 → 빠르고, 모바일에서도 가볍게 동작

UI 가 느려 보이거나 데이터가 오래돼 보이면 **DB 가 갱신 안 된 것**이지 UI 문제가 아니다. 그럴 땐 `run_research.py` 한 번 돌려주면 된다.

---

## 3. 매일 / 매주 권장 사용 흐름

### 3-1. 3분 — Daily Brief 만

가장 가볍게 쓰는 모드. 출근길 / 장 시작 직전 5분 안에 끝내기.

1. 메인 화면 → **헤드라인 한 줄**
2. **시장 환경 3블록** (자산 / 매크로 / 일정) 스캔
3. **Top picks 3~5개** 만 이름·company_type·action_tag 만 훑기
4. **Daily Alerts** 가 비어 있지 않다면 그것만 읽기 (urgent / staleness=Outdated / 신규 리스크)

여기까지 보면 "오늘 굳이 뭐 안 해도 되는 날인지 / 들춰볼 게 있는 날인지" 가 갈린다.

### 3-2. 15분 — 종목 1~2개 deep dive

뭔가 들춰볼 게 있는 날에 들어가는 모드.

1. Daily Brief 의 Top pick 또는 Alert 종목 클릭
2. **주가 차트** — 최근 6개월~3년 흐름과 마커(이벤트) 위치 확인
3. **종합 판단 (Conclusion)** — 한 줄 결론
4. **6요소 점수 막대** — 어디가 강하고 어디가 약한지
5. **핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항** — 3 카드 비교
6. **주요 뉴스** — Summary 만 읽고, Follow-up Items 가 액션 가능한지 점검
7. (필요 시) **Anti-Thesis / 가치평가 비교 / 재무 차트** 까지 들어가 검증

### 3-3. 주간 30분 — 의사결정 리뷰

주말에 돌리는 모드.

1. **Decision log** — 지난주 본인이 무엇을 봤고 무엇을 결정했는지 본인 노트 회고
2. **Performance tracking** — 1주/1개월 수익률, SPY/QQQ 대비 상대 성과
3. **outcome_tag 가 양/음 으로 갈린 종목** → 왜 갈렸는지 reflection
4. 큐레이션 (`src/curated.py`) 의 thesis_pillars 가 여전히 유효한지 점검

---

## 4. 화면별 의미와 활용 포인트

### 4-1. Daily Brief

| 블록 | 의미 | 활용 포인트 |
|---|---|---|
| 헤드라인 | 그 날을 한 줄로 | 첫인상 — 다른 블록을 어떤 톤으로 읽을지 결정 |
| 시장 환경 (자산) | SPY / QQQ / 채권 / 달러 / VIX 등 | "오늘은 risk-on/off?" 1분 안에 판단 |
| 시장 환경 (매크로) | 금리 / 인플레이션 / 정책 |  thesis 가 흔들리는지 체크 |
| 시장 환경 (일정) | 실적 발표 / FOMC / CPI 등 | 다가오는 catalyst 사전 인지 |
| Top picks | company_type + action_tag + 한 줄 rationale | 클릭해서 상세 진입할 후보 목록 |
| Alerts | urgent + staleness 필터링된 시급 항목 | 비어있지 않으면 그날의 0순위 |
| Check items | 매일 확인해야 할 시장 / 종목 질문들 | 일일 체크리스트로 사용 |

### 4-2. 종목 상세 (10 섹션)

종목 페이지는 위에서 아래로 정보 흐름이 자연스럽게 이어진다.

1. **이 회사는 쉽게 말해** — 큐레이션된 한 문장 회사 소개. 1차 컨텍스트.
2. **주가 차트** — 최근 흐름 + 이벤트 마커. 가격이 thesis 와 어디서 어긋났는지 시각화.
3. **6요소 점수** — Thesis / Evidence / Price / Financial / Event / Risk. **약한 축을 보는 게 더 중요**하다.
4. **핵심 투자 포인트 / 주요 리스크 / 확인 필요 사항** — 3 카드 형태. 점수의 근거.
5. **Anti-Thesis** — 이 thesis 가 틀릴 수 있는 시나리오들. 보유 종목 정기 점검 시 1순위.
6. **주요 이벤트** — 큐레이션된 사례 + 자동 수집된 이벤트. status/staleness/confidence 메타로 신뢰도 표시.
7. **주요 뉴스** — 최근 5개 뉴스를 Summary / Key Thesis / Follow-up Items 형태로 압축.
8. **리서치 품질** — 데이터 최신성 / 출처 신뢰도 / 종합 confidence. 의사결정 신중도 결정 지표.
9. **가치평가 비교** — PER / PSR / EV/EBITDA 의 시계열과 동종 비교.
10. **재무 차트** — 매출 / 영업이익 / FCF 추세. 정성 thesis 가 정량 지표로 확인되는지 체크.
11. **종합 판단 (Conclusion)** — 위 전체를 한 단락으로 압축. **여기만 보면 안 되고**, 위 데이터의 결론으로만 읽어야 한다.

#### 어떤 섹션을 우선 볼지 — 상황별

- **신규 편입 검토 시**: 1 → 3 → 4 → 5 → 9 → 10 → 11 (정성 → 정량 흐름)
- **기존 보유 종목 점검 시**: 5 (Anti-Thesis 먼저) → 7 (뉴스) → 6 (이벤트) → 11
- **뉴스가 시끄러운 날**: 7 (뉴스) → 8 (리서치 품질) → 11

### 4-3. 뉴스 카드 / 뉴스 상세

뉴스 카드는 영어 기사 → 한국어 리서치 메모로 압축한 영역이다. 카드 1장이 다음 구조다.

```
[제목]                                     [Thesis 영향 배지]
출처 · 날짜 · Confidence

Summary           — 5~10문장, 무엇이 새로운지 / 숫자 / 시장 반응 / thesis 영향
Key Thesis        — 2~4문장, 이 보도가 기존 thesis 와 어떻게 연결되는지
Follow-up Items   — bullet 4~6개, 후속 리서치 항목 (지표 / 가이던스 / 비교 데이터)

[기사 원문 보기 →]
```

#### 카드 신호 읽는 법

- **Confidence Low + content_availability=Title Only**: 잠정 정리. 단정 금지. 원문 확인 후 다시 점검.
- **Thesis 강화 / 약화 배지**: 단독으로 신뢰하지 말고 6요소 점수 변화와 함께 해석.
- **신규 리스크 + urgent 키워드**: 그 날의 0 순위 검토 대상.
- **Follow-up Items**: **이게 사실상 가장 액션 가능한 영역**. 매일 따로 메모해두면 그게 본인의 리서치 to-do.

---

## 5. 점수 / 분류 / Action Tag 해석법

### 5-1. 6요소 점수 (각 0~100)

| 요소 | 비중 | 의미 |
|---|---|---|
| Thesis Strength | 20% | 큐레이션 thesis_pillars + 카테고리 weight 기반 — 회사가 어떤 흐름의 수혜인지 |
| Evidence Strength | 15% | 최근 큐레이션 / 자동 이벤트의 confidence + 출처 + staleness 가중평균 |
| Price Opportunity | 20% | 52주 위치 / Dislocation / Valuation 디스카운트 |
| Financial Quality | 15% | ROE / FCF Yield / 매출 성장률 / 영업이익률 |
| Event Freshness | 15% | 최근 7~30일 이벤트 / 뉴스 활성도 |
| Risk Control | 15% | Anti-thesis 카운트 + urgent 리스크 + staleness 패널티 |

→ **합계가 같아도 분포가 다르면 의미가 다르다**.
- Thesis 90 / Price 30 → "좋은 회사인데 비싸다"
- Thesis 60 / Price 85 → "평범한 회사가 싸졌다"
- Risk 30 / 나머지 다 80 → "당장은 좋아도 anti-thesis 신호 강함" (보유 종목이면 점검 필수)

### 5-2. 7 company types

| Type | 설명 | 일반적 권장 행동 |
|---|---|---|
| Civilization Alpha | 산업 패러다임을 만드는 회사 (NVDA 같은) | 가격이 비싸도 보유 / 분할매수 |
| Quality Dislocation | 좋은 회사가 일시적 이유로 빠진 것 | 단계적 매수 — 단, 회복 트리거 확인 |
| Re-rating Candidate | thesis 변화로 multiple 자체가 바뀔 가능성 | 비중 작게, 이벤트 추적 |
| Structural Growth | 산업 구조적 성장 |  분할매수 + 보유 |
| Turnaround | 부진했다가 돌아서는 회사 | 분기 데이터로 확인 후 진입 |
| Too Crowded | 너무 인기 | 신규 진입 자제, 보유 시 분할 익절 |
| Avoid | 구조적 약점 | 신규 진입 금지 |

### 5-3. Action Tag

Action Tag 는 **명령이 아니라 질문**이다.

- **STRONG BUY** → "오늘 살까?" 가 아니라 "왜 강한 신호로 떴는지 — 6요소 어디가 80+ 인가?"
- **BUY / ACCUMULATE** → 분할매수 후보. 가격 위치 + Risk 점수 같이 봐야.
- **HOLD** → 점검만. 새로운 정보 없으면 행동 X.
- **REDUCE** → 비중 축소 검토. Anti-thesis 카드와 같이 봐야.
- **AVOID** → 신규 진입 금지. 이미 보유 중이라면 thesis 변화 트리거 확인.

**Action Tag 만 보고 행동하지 말고**, 클릭해서 6요소 점수 + Anti-thesis + 최근 뉴스 까지는 같이 본다.

---

## 6. 데이터 갱신 — 운영

### 6-1. 자동 (기본)

GitHub Actions 가 매일 새벽에 `run_research.py` 를 실행 → DB 업데이트 → Streamlit Cloud 가 자동 재배포한다.
사용자가 할 일은 없다.

### 6-2. 수동 — 강제로 다시 돌리기

뉴스가 너무 빠른 catalyst 가 발생했거나 주말 직접 점검할 때:

```bash
cd "/Users/hyeongucci/Documents/Claude/Projects/Alpha/daily_alpha_engine_research"
python3 run_research.py
git add data/alpha.db && git commit -m "manual update" && git push
```

push 하면 Streamlit Cloud 가 새 DB 로 재배포된다.

### 6-3. LLM 키 추가 (선택)

뉴스 한국어 메모 품질이 더 필요할 때 LLM 으로 업그레이드 가능.

```bash
# Streamlit Cloud → Secrets 에 추가
ANTHROPIC_API_KEY = "sk-..."
# 또는
OPENAI_API_KEY = "sk-..."
```

`news_summarizer.py` 가 자동 감지해서 LLM 호출 (기본은 rule-based 폴백). LLM 응답 실패 시 자동으로 rule-based 폴백.

---

## 7. 투자 의사결정에 적용하는 워크플로우

### 7-1. 신규 편입 검토 흐름 (15~30분)

1. Daily Brief 의 Top picks 에서 후보 1개 선정
2. 종목 상세 진입 → **이 회사는 쉽게 말해** 로 1차 이해
3. **6요소 점수** 분포 확인 — 약점이 receivable 한 약점인지 (예: Price 낮은 건 매수 기회)
4. **Anti-Thesis** 정독 — 가장 큰 risk 가 본인이 받아들일 수 있는지
5. **가치평가 비교 / 재무 차트** — 정성 thesis 가 정량 지표로 확인되는지
6. **주요 뉴스 Follow-up Items** 를 **본인 리서치 to-do** 로 옮겨 적기
7. Follow-up 1~2개라도 추가 확인 후 → 분할매수 / 관망 결정

### 7-2. 보유 종목 점검 흐름 (10분 / 종목)

1. **Anti-Thesis** 가 새로 추가됐는지
2. **주요 뉴스 / 이벤트 — Thesis 영향 배지** 가 약화 / 신규 리스크 인지
3. **Risk Control 점수** 가 떨어졌는지
4. **staleness** 가 Stale / Outdated 면 → 데이터가 낡은 것 (리스크 자체보다는 "리서치 갱신 필요" 신호)
5. 모두 그대로면 → **그 날은 행동 X** (이게 가장 흔한 결과여야 정상)

### 7-3. 시장 환경 점검 흐름 (5분)

매크로 / 자산 블록만 보고 다음 질문에 답변.
- 오늘이 risk-on 인가 risk-off 인가?
- 금리 / VIX 가 의미 있게 움직였는가?
- 다가오는 일정 (실적 / FOMC) 이 있는가?

답이 "특별한 변화 없음" 이면 **종목 단위로 들어가지 않고 끝낸다**. 이게 사용자에게 가장 큰 가치다.

---

## 8. Alpha 가 잘하는 것 / 약한 것

### 잘하는 것
- 매일 41 종목의 데이터를 동일한 기준으로 비교
- 영어 뉴스 본문 → 한국어 메모 압축
- 데이터 최신성 / 출처 / confidence 가 자동 표시
- 동일 잣대로 6요소 점수화 → 종목 간 비교 가능
- Follow-up Items 로 "다음에 뭘 봐야 하는지" 자동 큐잉

### 약한 것 (인지하고 사용해야 함)
- **개별 종목 뉴스 본문 전체** 까진 못 끌어옴 (Google News RSS 한계 — Snippet 이 짧을 때가 많다)
  → content_availability = Title Only / Snippet Only 인 카드는 단정 금지
- **시장 reflexivity** 는 못 본다 — 섹터 전반의 sentiment 변화는 사용자가 별도로 점검해야 함
- **rule-based 자동 분류**는 100% 정확하지 않다 → confidence Low 카드는 검증 필수
- **거시 / 정책 이벤트 영향** 은 큐레이션 데이터가 일부만 반영. 매크로 블록은 스냅샷에 가깝다.

→ 결론: **사전 스크리너로 쓰고, 결정의 마지막 한 발은 본인이 한다**.

---

## 9. 자주 쓰는 단축 흐름 (체크리스트)

### 매일 (3분)
- [ ] Daily Brief 헤드라인 + 시장 환경 + Alerts 만
- [ ] Alerts 가 비어있지 않으면 그 종목만 deep dive

### 매주 (30분)
- [ ] Decision log 의 본인 노트 점검
- [ ] Performance tracking — 1w / 1m / 3m 수익률 + SPY/QQQ 대비
- [ ] outcome_tag 가 음수인 종목 → reflection
- [ ] 큐레이션 thesis_pillars 한 번 훑기 (`src/curated.py`)

### 매월 (1시간)
- [ ] 41 종목 universe 자체 점검 — 추가 / 제거할 종목
- [ ] company_type 라벨이 여전히 맞는지 (Re-rating → Structural Growth 로 졸업 등)
- [ ] LLM API 키 활용 여부 결정 (월간 비용 vs 품질)

---

## 10. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| 데이터가 며칠째 그대로 | GitHub Actions 실패 | Actions 탭에서 로그 확인 → 보통 PAT 만료 |
| 뉴스 카드가 비어 있음 | 그 날 fetch 가 실패 | `python3 run_research.py` 수동 실행 |
| 종목 상세 차트가 비어 있음 | yfinance API 일시 차단 | 시간 두고 재시도 / VPN 변경 |
| 모바일에서 카드 깨짐 | 캐시 | 모바일 사파리 / 크롬 캐시 삭제 |
| Streamlit Cloud 에서 한국어 깨짐 | 폰트 미적용 | `.streamlit/config.toml` 점검 |
| 새 컬럼 (follow_up_items_ko 등) 미출력 | 기존 DB 마이그레이션 미실행 | `init_schema()` 호출되는 첫 실행 시 idempotent ALTER 자동 처리 |

---

## 11. 한 문장으로 다시

**Alpha 는 "이번 주 무엇을 우선 점검할지" 를 자동으로 정렬해주는 도구**다.
신호를 따라 매수하는 게 아니라, **신호로 시작해서 본인의 짧은 추가 리서치를 거치는 흐름**으로 쓰면 가장 효과적이다.

---

*마지막 업데이트: 2026-05-02*
