# Telegram 알림 셋업 가이드

## 1단계 — 봇 만들기 (이미 진행중)

폰 텔레그램 앱:
1. `@BotFather` 검색 → 대화 시작
2. `/newbot` 입력
3. 봇 이름 (display): 예 `Alpha Hyeongwoo Bot`
4. 봇 username (끝이 `bot`): 예 `alpha_hyeongwoo_bot`
5. **BOT_TOKEN** 받음 — 예 `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`
   - 외부 공유 금지

## 2단계 — Chat ID 받기

방법 A (간편):
1. 본인 만든 봇 검색 → 대화 시작 → 아무 메시지 1개 보냄 (예: "hi")
2. 브라우저에서 `https://api.telegram.org/bot<TOKEN>/getUpdates` 접속
3. JSON 응답에서 `"chat":{"id": 1234567}` 의 숫자가 **CHAT_ID**

방법 B (그룹용):
- 그룹 만들고 봇 초대 → `@userinfobot` 추가 → 그룹 ID (음수) 확인

## 3단계 — GitHub Secrets 등록

GitHub repo `hyeongwoohyeong/Alpha` → **Settings → Secrets and variables → Actions → New repository secret**

두 개 추가:
- `TELEGRAM_BOT_TOKEN` = BotFather 받은 토큰
- `TELEGRAM_CHAT_ID` = 본인 chat_id

## 4단계 — 첫 실행 (수동 테스트)

GitHub repo → **Actions → Telegram Alerts → Run workflow** 클릭

또는 mac 에서 로컬 테스트:
```bash
cd "/Users/hyeongucci/Documents/Claude/Projects/Alpha/daily_alpha_engine_research"
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python -m src.alert_engine
```

성공하면 텔레그램 봇에서 메시지 도착. 안 오면:
- 봇과 대화를 *최소 1번* 시작해야 받을 수 있음 (Telegram 정책)
- `getUpdates` 로 chat_id 다시 확인

## 5단계 — Streamlit Cloud Secrets (선택)

`today_decision.py` 에서 텔레그램 송신 부분이 fallback 으로 Streamlit secrets 도 읽음. 필요 시:
- Streamlit Cloud → App settings → Secrets → 토큰·chat_id 추가

## 룰 ON/OFF

`src/alert_engine.py` 상단 `RULES_ENABLED` dict:

```python
RULES_ENABLED = {
    "R1_alpha_bet_signals":   True,  # STOP/SELL/TRIM
    "R2_underlying_target":   True,  # SK하이닉스 ₩2.5M
    "R3_breakeven_cross":     True,  # 평단 돌파
    "R4_tqqq_sweet_spot":     True,  # QQQ DD -5~-15%
    "R5_btc_drawdown_deep":   True,  # BTC -70%+
    "R6_intraday_spike":      False, # 24h ±5%+ (spam 위험 — 기본 OFF)
    "R7_new_alpha_discovery": True,  # score≥80 + DD≤-10%
}
```

## Dedup

같은 룰은 4시간 이내 재발화 X (기본). 룰별 override 는 `_send_or_skip(.., dedup_hours=N)` 인자 조정:
- R2 본주 target: 24h (도달 trigger 자주 X)
- R4 TQQQ sweet spot: 48h
- R5 BTC -70%: 72h
- R6 24h spike: 6h

## 첫 1주일 운영 권장

- R1, R2 만 ON → 노이즈 확인
- R6 (intraday spike) 는 OFF 유지 — SOXL/BTC 자주 발화돼 spam 위험
- 알림 너무 잦다 → dedup_hours 증가 또는 룰 OFF
- 알림 너무 적다 → 룰 임계 조정 또는 더 enable
