# Alpha

미국주식 관심 Universe를 매일 추적하고, **실제 주가/뉴스 데이터**를 기반으로
오늘 봐야 할 종목 5개, 우량주 과매도 후보, 주요 이슈, 다음 행동을 한국어로 정리해주는
개인용 투자 리서치 엔진 MVP입니다.

이 앱은 자동 매수 추천기가 아닙니다. 매일 아침 3~5분 안에 아래 질문에 답할 수 있도록 돕습니다.

1. 오늘 시장에서 중요한 변화는 무엇인가?
2. 오늘 새롭게 볼 만한 종목은 무엇인가?
3. 관심종목 중 변화가 생긴 종목은 무엇인가?
4. 우량주 과매도 후보가 있는가?
5. 지금 내가 해야 할 리서치 행동은 무엇인가?

## 핵심 특징

- **3-Tier Discovery Engine**: 미국 상장주식 wide universe (~300 sample) 정량 스크리닝 → Tier 2 promotion → Tier 3 deep dive
- **4 Discovery 큐**: Quality Dislocation / Earnings Revision / Unusual Volume / Civilization Alpha
- **LLM 비용 0원 운영 가능**: `LLM_MODE=none` 모드에서 룰 기반으로만 작동. 같은 기사 URL 은 `article_summaries` 캐시로 재사용
- **3 LLM 모드**: `none` (비용 0) / `low_cost` (Tier 2 후보 일부) / `high_quality` (Deep Dive 후보)
- **실제 데이터** : `yfinance`로 실시간 주가, `Google News RSS`로 종목/테마 뉴스를 수집
- **에러 격리** : 특정 종목 데이터/뉴스 수집이 실패해도 앱은 죽지 않고 *Data Unavailable*로 표시

## 3-Tier 구조

```
Tier 1  Discovery   |  data/wide_universe.csv (~300 sample, Russell 3000 확장 가능)
                    |  → 정량 스크리닝 (가격 / 거래량 / 멀티플 / 재무비율)
                    |  → 4 큐 별 시그널 (Quality Dislocation / Earnings Revision / Unusual Volume / Civilization Alpha)
                    |  → discovery_scores 테이블
                    |  → Tier1 통합 상위 80 후보
                    |  ※ LLM 사용 금지, 뉴스 fetch 금지
                    ↓
Tier 2  Promotion   |  Tier 1 상위 80 후보 (core watchlist 제외)
                    |  → 뉴스 fetch (후보당 3건만)
                    |  → 한국어 요약 (LLM_MODE=none 이면 룰 기반)
                    |  → article_summaries 캐시 (같은 URL 재호출 X)
                    |  → Promotion Score 계산 → 상위 15 승격
                    |  → promotion_candidates 테이블 (promoted_to_deep_dive=1)
                    ↓
Tier 3  Deep Dive   |  core watchlist 42 + 승격 15
                    |  → 6요소 점수 / Anti-Thesis / Action Tag / Daily Brief
                    |  → 기존 종목 상세 화면 그대로 사용
```

## LLM 모드 / 비용 제어

환경변수로 제어합니다 (`.streamlit/secrets.toml` 또는 GitHub Actions secrets):

| 변수 | 기본값 | 의미 |
|---|---|---|
| `LLM_MODE` | `none` | `none` / `low_cost` / `high_quality` |
| `MAX_LLM_CALLS_PER_RUN` | `30` | 한 run 당 최대 LLM 호출 (캐시 hit 제외) |
| `ENABLE_SUMMARY_CACHE` | `true` | `article_summaries` 캐시 사용 여부 |
| `ENABLE_DISCOVERY` | `true` | Tier 1 Discovery 단계 on/off |
| `ENABLE_PROMOTION` | `true` | Tier 2 Promotion 단계 on/off |
| `WIDE_UNIVERSE_LIMIT` | `1500` | wide universe 처리 종목 상한 |
| `TIER1_TOP_K` | `80` | Tier 1 통합 후보 수 |
| `PROMOTE_TO_DEEP_DIVE_K` | `15` | Tier 2 → Tier 3 승격 수 |

`LLM_MODE=none` 또는 API key 미설정 시 — 자동으로 룰 기반 폴백. 엔진 전체 동작에는 영향 없음.

## 폴더 구조

```
daily_alpha_engine_research/
├── app.py                   # Streamlit UI (다크모드, 5개 메뉴)
├── requirements.txt
├── README.md
├── data/
│   ├── universe.csv         # 41종목 초기 유니버스
│   ├── daily_snapshots.csv  # 매일 자동 저장 (가격/score/tag)
│   └── decision_log.csv     # "오늘의 후보 저장" 로그
└── src/
    ├── universe.py          # 유니버스/테마 가중치/관심종목 관리
    ├── market_data.py       # yfinance 가격/리턴/drawdown/valuation
    ├── news_fetcher.py      # Google News RSS + 키워드 importance
    ├── scoring.py           # 6요소 Final Score & Action Tag
    ├── dislocation.py       # 우량주 과매도 후보 추출
    ├── brief_generator.py   # 데일리 브리프 (룰 기반 한국어)
    ├── stock_detail.py      # 종목 상세 (Bear/Base/Bull, Anti-Thesis)
    ├── engine.py            # 파이프라인 오케스트레이션 + CSV 저장
    └── utils.py             # 헬퍼/포맷
```

## 설치

Python 3.10+ 권장.

```bash
cd daily_alpha_engine_research
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 실행

Alpha 는 두 프로세스로 분리되어 있습니다.

### 1) 리서치 파이프라인 (`run_research.py`)
15 단계로 확장된 파이프라인.

1. core_universe 로드
2. wide_universe 로드 (Tier 1)
3. wide market data batch fetch
4. Discovery 4-queue scoring → discovery_scores
5-7. Promotion (뉴스 fetch + 한국어 요약 + Promotion Score) → promotion_candidates
8. core market data fetch (deep dive 대상)
9. core 뉴스 fetch + 요약 (캐시 사용)
10-12. 이벤트 클러스터링 / 스코어링 / 종목 리서치 본문
13. Daily Brief
14. Performance Tracking

```bash
# 전체 실행 (매일 한 번) — Discovery 포함
python3 run_research.py

# 특정 종목만 (Discovery 자동 skip — core/지정 종목만 deep dive)
python3 run_research.py --ticker NFLX --ticker AXON

# Discovery / Promotion 단계 생략 (core watchlist 만 — 빠름)
python3 run_research.py --skip-discovery

# Wide universe 가격 fetch 까지 생략 (개발/테스트)
python3 run_research.py --skip-discovery --skip-wide-fetch

# 가격만 빠른 갱신 (장중)
python3 run_research.py --skip-news --skip-discovery

# 검증 (DB 안 쓰고 출력만)
python3 run_research.py --dry-run --skip-wide-fetch
```

전체 실행 시간 (LLM_MODE=none 기준):
- core 만: 30~90초
- Discovery 포함: 5~15분 (wide universe 300개 가격 fetch + 80개 뉴스 fetch)

처음 한 번은 30~90초가 걸립니다. 두 번째 실행부터는 이미 수집된 데이터를 UPSERT.

### 2) UI (`app.py`)
DB 에서 결과만 조회해서 표시합니다. 네트워크 호출 없이 즉시 로딩.

```bash
streamlit run app.py
```

브라우저에서 자동으로 `http://localhost:8501` 가 열립니다.

UI 우측 상단의 **데이터 및 뉴스 업데이트** 버튼을 누르면 `run_research.py`
가 subprocess 로 실행되어 DB 가 갱신됩니다.

## 자동화 (매일 새벽 갱신)

매일 한국시간 오전 7:30 자동 실행하려면 다음 중 하나를 선택하세요.

### macOS / Linux cron

```bash
crontab -e
# 분 시 일 월 요일  명령
30 7 * * *  cd /path/to/daily_alpha_engine_research && /usr/bin/python3 run_research.py >> data/cron.log 2>&1
```

### GitHub Actions
`.github/workflows/daily_research.yml` 작성 (예시):

```yaml
name: Daily Research
on:
  schedule:
    - cron: "30 22 * * *"   # UTC 22:30 = KST 07:30
  workflow_dispatch:
jobs:
  research:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python run_research.py
      - uses: actions/upload-artifact@v4
        with:
          name: alpha-db
          path: data/alpha.db
```

### Render Cron Job / Railway Scheduled Job
- **Render**: Cron Job 서비스 생성 → Schedule `30 22 * * *` → Command `python run_research.py`
- **Railway**: Scheduled Job 추가 → 동일 cron 표현식

### 배포 권장 구성
- **Streamlit Cloud**: 단순 UI 확인용 (DB 갱신은 별도 Job 필요)
- **실제 배포**: Render / Railway / VPS (cron + Streamlit 동시 실행 가능)
- **DB**: 시작은 SQLite (`data/alpha.db`). 다중 사용자 / 동시 쓰기 필요 시 PostgreSQL 로 전환 (psycopg2 + DATABASE_URL 환경변수)

---

## 핸드폰에서 보기 (Streamlit Cloud 배포, 5분)

### Step 1 — GitHub 리포지토리 만들기
1. github.com 에서 New repository → 이름 예: `alpha-research`
2. Public 또는 Private 선택 (Streamlit Cloud 무료 티어는 둘 다 지원)

### Step 2 — 코드 push
```bash
cd /Users/hyeongucci/Documents/Claude/Projects/Alpha/daily_alpha_engine_research

git init
git add .
git commit -m "init: Alpha research engine"

git remote add origin https://github.com/<your-username>/alpha-research.git
git branch -M main
git push -u origin main
```

> `data/alpha.db` 도 함께 push 됩니다. 이 파일이 Streamlit Cloud 가 화면에 보여줄 데이터 소스입니다. WAL/journal 파일은 `.gitignore` 로 자동 제외됩니다.

### Step 3 — Streamlit Cloud 배포
1. https://share.streamlit.io 접속 → GitHub 계정으로 로그인
2. **New app** 클릭
3. 입력값:
   - Repository: `<your-username>/alpha-research`
   - Branch: `main`
   - Main file path: `app.py`
4. **Deploy** 클릭
5. 약 1~2분 후 `https://<your-username>-alpha-research.streamlit.app` URL 발급

### Step 4 — 핸드폰에서 접속
- iOS Safari / Android Chrome 에서 위 URL 입력
- 사이드바는 좌상단 햄버거 메뉴로 자동 이동
- 폰트/카드 padding 은 모바일 폴백 CSS 가 자동 적용

### Step 5 — 매일 자동 갱신 활성화 (선택)
GitHub Actions 가 매일 한국시간 오전 7:30 에 `run_research.py` 를 실행하고 `alpha.db` 를 자동 commit 합니다. Streamlit Cloud 는 commit 을 감지해 화면을 자동 갱신.

1. 리포지토리의 **Settings → Actions → General → Workflow permissions** → **Read and write permissions** 선택
2. **Actions** 탭 → `Daily Research` 워크플로우 → **Enable workflow**
3. 첫 실행은 **Run workflow** 버튼으로 수동 트리거 (정상 실행 확인)
4. 이후 매일 자동 실행

### 핸드폰에서 직접 갱신하기
앱 우측 상단 **Update** 버튼을 누르면 그 자리에서 `run_research.py` 를 실행합니다. 다만 **Streamlit Cloud 의 무료 티어는 컨테이너가 재시작되면 변경사항이 사라집니다.** 영구 갱신은 GitHub Actions 자동 실행 또는 GitHub UI 에서 워크플로우 수동 트리거를 사용하세요.

### 보안 주의
- `.streamlit/secrets.toml` 은 `.gitignore` 로 제외되어 있음 (API Key 보관용 — 향후 LLM 연동 시 사용)
- 리포지토리를 **Private** 으로 두면 다른 사람은 화면을 못 봄. 본인만 보려면 Private 권장.

## 데이터 새로고침

사이드바의 **🔄 데이터 새로고침** 버튼을 누르면 yfinance와 Google News RSS에서 다시 데이터를 가져옵니다. 캐시가 30분으로 설정되어 있어, 동일 토큰에서는 호출이 줄어듭니다.

## universe.csv 수정 방법

`data/universe.csv` 를 직접 편집하면 됩니다.

```csv
ticker,name_ko,name_en,theme,category
NVDA,엔비디아,NVIDIA,ai_semiconductor,AI Infrastructure
...
```

- `ticker` : yfinance에서 인식 가능한 심볼 (예: `NVDA`, `BRK-B`, `BTC-USD`)
- `name_ko` : 한국어 표기 (UI에서 항상 `한국어명 (티커)` 로 표시됨)
- `name_en` : 뉴스 검색 시 보조 키워드로 사용
- `theme` : `src/universe.py` 의 `THEME_WEIGHTS` 키 중 하나
- `category` : 자유롭게 (예: `AI Infrastructure`, `Quality Platform`)

새 테마를 추가하면 `src/universe.py` 의 `THEME_WEIGHTS`, `THEME_LABEL_KO`, `news_fetcher.py` 의 `THEME_QUERIES` 에도 항목을 추가하세요.

## Action Tag 정의

| Tag | 기준 |
| --- | --- |
| **Research Now** | Final Score ≥ 60, Risk ≤ 25, 모멘텀/뉴스/테마/디스로케이션 중 2개 이상 강함 |
| **Quality Dislocation** | 카테고리 리더 + 52주 고점 대비 -25%~-42% + urgent risk 키워드 없음 |
| **Wait for Entry** | 좋은 테마지만 단기 급등 (3M +30% 이상) |
| **Watchlist** | 명확한 신호는 부족하나 카테고리/테마 가치 보유 |
| **Too Crowded** | 6M +80% 또는 1Y +150%, 동시에 forward PE > 45 |
| **Need Thesis Check** | 52주 -30% 이상 하락 + 뉴스 시그널 약함 |
| **Avoid** | urgent risk 키워드(fraud, investigation, bankruptcy 등) 또는 risk score ≥ 60 |
| **Data Unavailable** | yfinance 가격 수집 실패 |

## 향후 LLM API 붙이는 방법

룰 기반 한국어 코멘트 → LLM 자연어 코멘트로 업그레이드하는 가장 짧은 경로:

1. `src/brief_generator.py`
   - `generate_one_liner(rows, picks)` : 오늘의 한 줄 결론
   - `why_to_watch(row)` / `biggest_risk(row)` / `next_action(row)` : 카드 텍스트
2. `src/stock_detail.py`
   - `thesis_one_liner(row)` / `attractiveness_bullets(row)` / `risk_bullets(row)` /
     `scenarios(row)` / `anti_thesis(row)` : 종목 상세 텍스트

이 함수들의 시그니처를 그대로 유지한 채 내부 구현을 LLM 호출로 교체하면 됩니다. 예시:

```python
# 환경변수 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 사용
import os
from anthropic import Anthropic

def generate_one_liner(rows, picks):
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""다음 데이터로 오늘의 한 줄 결론을 한국어로 만들어 주세요.\n
    {format_picks(picks)}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()
```

API Key가 없으면 자동으로 룰 기반 함수로 fallback 하도록 try/except로 감싸는 패턴을 권장합니다.

## 페이지 이동 시 스크롤 동작

모든 페이지 이동(사이드바 메뉴 클릭 / 종목 카드 상세 보기 / 우량주 과매도·관심종목 카드 상세 보기 / 종목 상세에서 다른 종목으로 변경)에서 **항상 화면 최상단으로 자동 스크롤**됩니다. 통일 함수 `navigate_to(nav_key, ticker=None)` 와 `scroll_to_top()` (3중 fallback + setTimeout 50/200ms)이 `app.py` 에 정의되어 있습니다. 라우팅 직전 한 곳에서만 플래그를 처리해 모든 페이지에 일관 적용됩니다.

### 페이지 이동 테스트 시나리오

코드 수정 후 아래 시나리오를 순서대로 확인합니다.

1. 오늘의 투자 브리프에서 화면 끝까지 스크롤
2. 종목 카드의 [상세 보기] 클릭 → 종목 상세 화면이 페이지 최상단(종목명/투자 판단 헤더)부터 보여야 함
3. 종목 상세에서 화면 끝까지 스크롤
4. 사이드바에서 [우량주 과매도] 클릭 → 우량주 과매도 화면이 최상단부터 보여야 함
5. 우량주 과매도에서 [상세 보기] 클릭 → 종목 상세가 최상단부터 보여야 함
6. 종목 상세 selectbox 에서 다른 종목으로 변경 → 새 종목의 화면 최상단부터 보여야 함
7. 사이드바에서 [관심종목] 클릭 → 최상단부터 보여야 함
8. 관심종목 화면에서 카드 [상세 보기] 클릭 → 종목 상세 최상단부터 보여야 함
9. 사이드바에서 [회고 리포트] 클릭 → 최상단부터 보여야 함
10. 회고 리포트 → [오늘의 투자 브리프] 클릭 → 최상단부터 보여야 함
11. 어떤 페이지에서든 같은 메뉴를 다시 클릭해도 최상단으로 이동해야 함

성공 기준: 모든 시나리오에서 첫 화면이 해당 페이지의 최상단이어야 합니다. 이전 페이지의 스크롤 위치가 유지되면 실패입니다.

## 한계 / 주의

- yfinance 의 `info` 는 시점에 따라 일부 필드가 비어있을 수 있어 `quality_score`가 중립 처리되는 경우가 있습니다.
- Google News RSS는 비공식 엔드포인트라서 일부 시간대에 차단/지연이 발생할 수 있습니다. 본 앱은 종목별 뉴스가 0건이어도 동작합니다.
- 본 앱은 **개인 리서치 도구** 입니다. 매수/매도 결정은 직접 하시기 바랍니다.
