"""
CGV IMAX 예매 오픈 알리미 (v2 — cgv.co.kr JSON API 사용)
- /api/v1/booking/searchMovScnInfo 로 극장/날짜별 상영정보 조회
- 키워드 영화의 IMAX관 회차가 열리면 텔레그램으로 알림
- 알림 보낸 (영화, 날짜)는 state.json에 기록해 중복 방지

환경변수:
  TELEGRAM_BOT_TOKEN : @BotFather 로 만든 봇 토큰
  TELEGRAM_CHAT_ID   : 알림 받을 채팅 ID
  CGV_CUST_NO        : (선택) CGV 고객번호 — 없이 실패하면 Secrets로 추가
"""

import json
import os
import sys
from datetime import date, timedelta

import requests

# ===================== 설정 =====================
CO_CD = "A420"
SITE_NO = "0013"        # 용산아이파크몰
MOVIE_KEYWORD = "오디세이"  # 영화 제목에 포함될 키워드
DAYS_AHEAD = 21         # 오늘부터 며칠 뒤까지 체크
STATE_FILE = "state.json"
# ================================================

API_URL = "https://cgv.co.kr/api/v1/booking/searchMovScnInfo"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://cgv.co.kr/cnm/movieBook/cinema",
}


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(state: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(state), f, ensure_ascii=False, indent=2)


def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=10,
    )
    resp.raise_for_status()


def fmt_time(t: str) -> str:
    """'0630' -> '06:30' (25시 표기 등 그대로 유지)"""
    return f"{t[:2]}:{t[2:]}" if t and len(t) == 4 else t


def check_date(target: date) -> list[str]:
    """해당 날짜에서 (키워드 영화 + IMAX관) 회차 목록을 반환."""
    params = {
        "coCd": CO_CD,
        "siteNo": SITE_NO,
        "scnYmd": target.strftime("%Y%m%d"),
        "rtctlScopCd": "08",
    }
    cust_no = os.environ.get("CGV_CUST_NO", "")
    if cust_no:
        params["custNo"] = cust_no

    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f"[debug] {target} status={resp.status_code} head={resp.text[:120]!r}")
        resp.raise_for_status()

    payload = resp.json()
    rows = payload.get("data") or []
    imax = sorted({
        f'{r.get("scnsNm","")}/{r.get("expoProdNm","")}'
        for r in rows
        if "IMAX" in ((r.get("scnsNm") or "") + (r.get("scnsEnm") or "")).upper()
    })
    print(f"[debug] {target} rows={len(rows)} imax={imax}")

    found = []
    for row in rows:
        hall = (row.get("scnsNm") or "") + (row.get("scnsEnm") or "")
        title = row.get("expoProdNm") or row.get("movNm") or ""
        if "IMAX" not in hall.upper():
            continue
        if MOVIE_KEYWORD not in title:
            continue
        start = fmt_time(row.get("scnsrtTm", ""))
        free = row.get("frSeatCnt", "?")
        total = row.get("stcnt", "?")
        found.append(f"{start} (잔여 {free}/{total}석)")
    return found


def main() -> None:
    state = load_state()
    new_alerts = []

    today = date.today()
    for offset in range(DAYS_AHEAD + 1):
        target = today + timedelta(days=offset)
        key = f"{MOVIE_KEYWORD}:{target.isoformat()}"
        if key in state:
            continue
        try:
            hits = check_date(target)
        except Exception as e:  # 오류는 다음 실행에 재시도
            print(f"[warn] {target} check failed: {e}", file=sys.stderr)
            continue
        if hits:
            state.add(key)
            new_alerts.append(
                f"📅 {target.strftime('%m/%d(%a)')}\n" + "\n".join(hits)
            )

    if new_alerts:
        msg = (
            f"🎬 CGV 용산 IMAX 예매 오픈!\n"
            f"영화: {MOVIE_KEYWORD}\n\n" + "\n\n".join(new_alerts) +
            "\n\n▶ https://cgv.co.kr/cnm/movieBook/cinema"
        )
        send_telegram(msg)
        save_state(state)
        print("alert sent")
    else:
        print("no new openings")


if __name__ == "__main__":
    main()
