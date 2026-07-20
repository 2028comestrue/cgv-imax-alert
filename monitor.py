"""
CGV IMAX 예매 오픈 알리미
- 지정한 극장/날짜의 상영시간표를 조회해서
- 원하는 영화의 IMAX 회차가 열리면 텔레그램으로 알림을 보냄
- 이미 알림 보낸 (영화, 날짜) 조합은 state.json에 기록해 중복 방지

환경변수:
  TELEGRAM_BOT_TOKEN : @BotFather 로 만든 봇 토큰
  TELEGRAM_CHAT_ID   : 알림 받을 채팅 ID
"""

import json
import os
import sys
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

# ===================== 설정 =====================
THEATER_CODE = "0013"   # 용산아이파크몰 (CGV 극장코드)
AREA_CODE = "01"        # 서울
MOVIE_KEYWORD = "호프"     # 영화 제목에 포함될 키워드 (예: "듄", "아바타")
DAYS_AHEAD = 14         # 오늘부터 며칠 뒤까지 체크할지
STATE_FILE = "state.json"
# ================================================

SCHEDULE_URL = (
    "http://www.cgv.co.kr/common/showtimes/iframeTheater.aspx"
    "?areacode={area}&theatercode={theater}&date={date}"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "http://www.cgv.co.kr/theaters/",
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


def check_date(target: date) -> list[str]:
    """해당 날짜 시간표에서 (키워드 영화 + IMAX관) 회차 목록을 반환."""
    url = SCHEDULE_URL.format(
        area=AREA_CODE, theater=THEATER_CODE, date=target.strftime("%Y%m%d")
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    found = []
    # 영화별 블록: div.col-times 안에 제목(strong)과 관/회차 정보가 있음
    for block in soup.select("div.col-times"):
        title_el = block.select_one("div.info-movie strong")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if MOVIE_KEYWORD not in title:
            continue

        for hall in block.select("div.type-hall"):
            hall_name = hall.get_text(" ", strip=True)
            if "IMAX" not in hall_name.upper():
                continue
            times = [t.get_text(strip=True) for t in hall.select("a span.time")]
            if times:
                found.append(f"{title} | {', '.join(times)}")
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
        except Exception as e:  # 네트워크/파싱 오류는 다음 실행에 재시도
            print(f"[warn] {target} check failed: {e}", file=sys.stderr)
            continue
        if hits:
            state.add(key)
            lines = "\n".join(hits)
            new_alerts.append(f"📅 {target.strftime('%m/%d(%a)')}\n{lines}")

    if new_alerts:
        msg = (
            f"🎬 CGV 용산 IMAX 예매 오픈!\n"
            f"영화: {MOVIE_KEYWORD}\n\n" + "\n\n".join(new_alerts) +
            "\n\n▶ http://www.cgv.co.kr/ticket/"
        )
        send_telegram(msg)
        save_state(state)
        print("alert sent")
    else:
        print("no new openings")


if __name__ == "__main__":
    main()
