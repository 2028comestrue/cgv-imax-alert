"""
CGV 용산 특별관 시간표 오픈 알리미 (v6 — 간소화판)

무엇을 하나:
  용산아이파크몰의 IMAX / ULTRA 4DX / SCREENX 관에
  "그 날짜 회차가 열렸는가"만 감지해서 텔레그램으로 알림.
  잔여석은 안 봄. 날짜 + 관 + (참고용) 상영작 목록만 보냄.

v5 대비:
  - 잔여석 관련 필드 전부 제거 (검증 안 된 필드였음)
  - 영화 키워드 필터 기본 해제 → 관 단위로 감지 (키워드 오타로 놓치는 사고 방지)
  - state 키가 "관|날짜" 로 단순화
  - 응답 구조가 바뀌어도 --dump 로 바로 확인 가능

환경변수:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

사용:
  python monitor.py                    # 1회 실행
  python monitor.py --loop             # 상주 실행 (권장)
  python monitor.py --interval 30      # loop 간격(초)
  python monitor.py --dump             # 응답 원본 구조 확인
  python monitor.py --test             # 텔레그램 연결만 테스트
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ===================== 설정 =====================
CO_CD = "A420"
SITE_NO = "0013"                 # 용산아이파크몰

# 감시할 특별관. 위에서부터 먼저 매칭됨 (순서 중요)
# 비교는 공백·하이픈 제거 + 대문자 기준
WATCH_FORMATS: list[tuple[str, tuple[str, ...]]] = [
    ("IMAX",      ("IMAX",)),
    # 주의: CGV API는 용산 3관을 "4DX관"으로만 표기한다 (ULTRA 4DX는 브랜드명일 뿐).
    # 용산 전용이므로 일반 "4DX"도 여기에 매핑한다. 다른 지점으로 바꾸면 재검토 필요.
    ("ULTRA 4DX", ("ULTRA4DX", "4DXSCREEN", "4DXWITH", "울트라4DX", "4DX")),
    ("SCREENX",   ("SCREENX", "스크린X")),
]

# 특정 날짜만 볼 거면 여기에 나열 (비워두면 오늘부터 DAYS_AHEAD일)
TARGET_DATES: list[date] = [
    # date(2026, 8, 8),
    # date(2026, 8, 9),
]
DAYS_AHEAD = 21

# 특정 영화만 볼 거면 키워드 지정 (빈 리스트 = 전부)
MOVIE_KEYWORDS: list[str] = [스파이더맨]

EMPTY_STREAK_STOP = 2            # 빈 날짜 연속 N개면 그 뒤는 안 봄
INTERVAL_SEC = 60
JITTER_SEC = 15
STATE_FILE = "state.json"

# ---- 응답 필드명. --dump 로 확인 후 맞지 않으면 여기만 고치면 됨 ----
FIELD_HALL = ("scnsNm", "scnsEnm", "screenNm", "theaterNm")   # 관 이름 후보
FIELD_TITLE = ("expoProdNm", "movNm", "movieNm", "prodNm")    # 영화 제목 후보
FIELD_TIME = ("scnsrtTm", "scnStartTm", "startTime")          # 상영 시작 시각 후보
# ================================================

API_URL = "https://cgv.co.kr/api/v1/booking/searchMovScnInfo"
BOOK_URL = "https://cgv.co.kr/cnm/movieBook/cinema"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": BOOK_URL,
}
WEEKDAY_KR = "월화수목금토일"
KST = ZoneInfo("Asia/Seoul")


def today_kst() -> date:
    """GitHub 러너는 UTC라 date.today()가 한국보다 하루 전일 수 있다."""
    return datetime.now(KST).date()

_session = requests.Session()
_session.headers.update(HEADERS)


# --------------------- state ---------------------
def load_state() -> set[str]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[warn] state 읽기 실패, 새로 시작: {e}", file=sys.stderr)
    return set()


def save_state(state: set[str]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(state), f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def state_key(fmt: str, day: date) -> str:
    return f"{fmt}|{day.isoformat()}"


# --------------------- util ---------------------
def _flat(s: str) -> str:
    return "".join(s.split()).replace("-", "").replace("_", "").upper()


def pick(row: dict, candidates: tuple[str, ...]) -> str:
    """후보 필드명 중 값이 있는 첫 번째를 반환."""
    for key in candidates:
        v = row.get(key)
        if v:
            return str(v)
    return ""


def detect_format(row: dict) -> str | None:
    """관 이름에서 특별관 라벨을 뽑는다. 감시 대상 아니면 None."""
    flat = _flat("".join(str(row.get(k) or "") for k in FIELD_HALL))
    for label, keys in WATCH_FORMATS:
        if any(_flat(k) in flat for k in keys):
            return label
    return None


def fmt_day(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}({WEEKDAY_KR[d.weekday()]})"


def fmt_time(t: str) -> str:
    return f"{t[:2]}:{t[2:]}" if t and len(t) == 4 and t.isdigit() else t


def target_dates() -> list[date]:
    if TARGET_DATES:
        return sorted(d for d in TARGET_DATES if d >= today_kst())
    today = today_kst()
    return [today + timedelta(days=i) for i in range(DAYS_AHEAD + 1)]


def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = _session.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=10,
    )
    resp.raise_for_status()


# --------------------- fetch ---------------------
def extract_rows(payload) -> list[dict]:
    """응답에서 회차 리스트를 찾아낸다. 구조가 바뀌어도 웬만하면 잡히도록."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "list", "resultData", "result", "items"):
        v = payload.get(key)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
        if isinstance(v, dict):
            found = extract_rows(v)
            if found:
                return found
    # 마지막 수단: dict 값 중 dict 리스트인 것 아무거나
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def fetch_rows(target: date) -> list[dict]:
    params = {
        "coCd": CO_CD,
        "siteNo": SITE_NO,
        "scnYmd": target.strftime("%Y%m%d"),
        "rtctlScopCd": "08",
    }
    resp = _session.get(API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return extract_rows(resp.json())


# --------------------- scan ---------------------
def scan(state: set[str], verbose: bool = True) -> dict[date, dict[str, list[tuple[str, str]]]]:
    """
    반환: {날짜: {관라벨: [상영작, ...]}}  — 새로 열린 것만
    """
    alerts: dict[date, dict[str, list[tuple[str, str]]]] = {}
    empty_streak = 0
    calls = 0

    for target in target_dates():
        pending = [
            label for label, _ in WATCH_FORMATS
            if state_key(label, target) not in state
        ]
        if not pending:
            continue

        try:
            rows = fetch_rows(target)
            calls += 1
        except Exception as e:
            print(f"[warn] {target} 조회 실패: {e}", file=sys.stderr)
            time.sleep(3)
            continue

        if not rows:
            empty_streak += 1
            if empty_streak >= EMPTY_STREAK_STOP and not TARGET_DATES:
                break                       # 예매 지평선 바깥
            continue
        empty_streak = 0

        # 관별로 상영작 수집
        by_fmt: dict[str, set[tuple[str, str]]] = {}
        for r in rows:
            label = detect_format(r)
            if not label:
                continue
            title = pick(r, FIELD_TITLE) or "(제목없음)"
            if MOVIE_KEYWORDS and not any(k in title for k in MOVIE_KEYWORDS):
                continue
            hall = pick(r, FIELD_HALL) or label
            by_fmt.setdefault(label, set()).add((hall, title))

        if verbose:
            summary = ", ".join(f"{k}({len(v)})" for k, v in sorted(by_fmt.items())) or "-"
            print(f"[debug] {target} rows={len(rows)} → {summary}")

        for label in pending:
            titles = by_fmt.get(label)
            if titles:
                state.add(state_key(label, target))
                alerts.setdefault(target, {})[label] = sorted(titles)

    if verbose:
        print(f"[debug] API 호출 {calls}회")
    return alerts


def build_message(alerts: dict[date, dict[str, list[tuple[str, str]]]]) -> str:
    blocks = []
    for day in sorted(alerts):
        lines = [f"📅 {fmt_day(day)}"]
        for label in [lbl for lbl, _ in WATCH_FORMATS if lbl in alerts[day]]:
            lines.append(f"  • {label}")
            for hall, title in alerts[day][label]:
                lines.append(f"      {hall} — {title}")
        blocks.append("\n".join(lines))
    return (
        "🚨 CGV 용산 특별관 시간표 오픈!\n\n"
        + "\n\n".join(blocks)
        + f"\n\n▶ {BOOK_URL}"
    )


def run_once(state: set[str], verbose: bool = True) -> bool:
    alerts = scan(state, verbose=verbose)
    stamp = datetime.now().strftime("%H:%M:%S")
    if not alerts:
        print(f"[{stamp}] no new openings")
        return False
    send_telegram(build_message(alerts))
    save_state(state)
    print(f"[{stamp}] alert sent — {len(alerts)}개 날짜")
    return True


# --------------------- dump ---------------------
def dump(target: date) -> None:
    """응답 구조 / 관 이름 / 필드명 확인용."""
    params = {
        "coCd": CO_CD, "siteNo": SITE_NO,
        "scnYmd": target.strftime("%Y%m%d"), "rtctlScopCd": "08",
    }
    resp = _session.get(API_URL, params=params, timeout=15)
    print(f"URL    : {resp.url}")
    print(f"status : {resp.status_code}")
    try:
        payload = resp.json()
    except Exception:
        print("JSON 아님. 응답 앞부분:")
        print(resp.text[:1000])
        return

    if isinstance(payload, dict):
        print(f"최상위 키: {list(payload.keys())}")
    rows = extract_rows(payload)
    print(f"추출된 row 수: {len(rows)}")
    if not rows:
        print("\n-- 응답 전체 (앞부분) --")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        return

    print("\n-- 관 이름 / detect_format 결과 --")
    seen = {}
    for r in rows:
        name = " / ".join(str(r.get(k) or "") for k in FIELD_HALL)
        seen[name] = detect_format(r)
    for name, label in sorted(seen.items()):
        print(f"  {name:40} → {label}")

    print("\n-- row 샘플 (전체 필드) --")
    print(json.dumps(rows[0], ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=INTERVAL_SEC)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--days", type=int, default=0, help="--dump 시 오늘+N일")
    ap.add_argument("--test", action="store_true", help="텔레그램 연결만 확인")
    args = ap.parse_args()

    if args.dump:
        dump(today_kst() + timedelta(days=args.days))
        return
    if args.test:
        send_telegram("✅ CGV 알리미 연결 테스트")
        print("텔레그램 전송 성공")
        return

    state = load_state()

    if not args.loop:
        run_once(state)
        return

    print(f"loop 시작 — {args.interval}초(±{JITTER_SEC}s) 간격, Ctrl+C 종료")
    while True:
        try:
            run_once(state, verbose=False)
        except KeyboardInterrupt:
            print("\n종료")
            return
        except Exception as e:
            print(f"[warn] 사이클 실패: {e}", file=sys.stderr)
        try:
            time.sleep(max(10, args.interval + random.randint(-JITTER_SEC, JITTER_SEC)))
        except KeyboardInterrupt:
            print("\n종료")
            return


if __name__ == "__main__":
    main()
