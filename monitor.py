"""
CGV IMAX 예매 오픈 알리미 (v4 — 다중 영화 키워드 지원)
- cgv.co.kr JSON API로 극장/날짜별 상영정보 조회 (날짜당 1회 호출)
- MOVIE_KEYWORDS 중 하나라도 IMAX관에 열리면 텔레그램 알림
- 알림 보낸 (영화, 날짜)는 state.json에 기록해 중복 방지

환경변수:
  TELEGRAM_BOT_TOKEN : @BotFather 로 만든 봇 토큰
  TELEGRAM_CHAT_ID   : 알림 받을 채팅 ID
"""

import json
import os
import sys
from datetime import date, timedelta

import requests

# ===================== 설정 =====================
CO_CD = "A420"
SITE_NO = "0013"                    # 용산아이파크몰
MOVIE_KEYWORDS = ["오디세이", "스파이더맨-브랜드 뉴 데이"]  # 감시할 영화 키워드들 (원하는 만큼 추가)
DAYS_AHEAD = 21                     # 오늘부터 며칠 뒤까지 체크
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
    """'0630' -> '06:30'"""
    return f"{t[:2]}:{t[2:]}" if t and len(t) == 4 else t


def fetch_imax_rows(target: date) -> list[dict]:
    """해당 날짜의 IMAX관 상영 row들을 반환 (API 호출은 날짜당 1회)."""
    params = {
        "coCd": CO_CD,
        "siteNo": SITE_NO,
        "scnYmd": target.strftime("%Y%m%d"),
        "rtctlScopCd": "08",
    }
    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f"[debug] {target} status={resp.status_code} head={resp.text[:120]!r}")
        resp.raise_for_status()

    rows = resp.json().get("data") or []
    imax_rows = [
        r for r in rows
        if "IMAX" in ((r.get("scnsNm") or "") + (r.get("scnsEnm") or "")).upper()
    ]
    print(f"[debug] {target} rows={len(rows)} imax_rows={len(imax_rows)}")
    return imax_rows


def main() -> None:
    state = load_state()
    # 영화별로 알림 내용을 모음: {키워드: [날짜 블록, ...]}
    alerts: dict[str, list[str]] = {}

    today = date.today()
    for offset in range(DAYS_AHEAD + 1):
        target = today + timedelta(days=offset)
        # 이 날짜를 아직 알림 안 보낸 키워드가 하나라도 있을 때만 조회
        pending = [
            kw for kw in MOVIE_KEYWORDS
            if f"{kw}:{target.isoformat()}" not in state
        ]
        if not pending:
            continue
        try:
            imax_rows = fetch_imax_rows(target)
        except Exception as e:  # 오류는 다음 실행에 재시도
            print(f"[warn] {target} check failed: {e}", file=sys.stderr)
            continue

        for kw in pending:
            hits = [
                f'{fmt_time(r.get("scnsrtTm", ""))} '
                f'(잔여 {r.get("frSeatCnt", "?")}/{r.get("stcnt", "?")}석)'
                for r in imax_rows
                if kw in (r.get("expoProdNm") or r.get("movNm") or "")
            ]
            if hits:
                state.add(f"{kw}:{target.isoformat()}")
                alerts.setdefault(kw, []).append(
                    f"📅 {target.strftime('%m/%d(%a)')}\n" + "\n".join(hits)
                )

    if alerts:
        sections = [
            f"🎬 {kw}\n\n" + "\n\n".join(blocks)
            for kw, blocks in alerts.items()
        ]
        msg = (
            "🚨 CGV 용산 IMAX 예매 오픈!\n\n" + "\n\n——————\n\n".join(sections) +
            "\n\n▶ https://cgv.co.kr/cnm/movieBook/cinema"
        )
        send_telegram(msg)
        save_state(state)
        print("alert sent")
    else:
        print("no new openings")


if __name__ == "__main__":
    main()
