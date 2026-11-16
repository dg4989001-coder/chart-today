#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""휴장일 판별 — 사고 15·16(2026-09-24·25 추석 연휴) 대응

휴장일에도 파이프라인이 돌아서 전 거래일 데이터로 '새 원고'를 다시 만들던 문제를 막는다.
휴장일 목록을 따로 관리하지 않고, Daum 삼성전자(A005930) 시세의 tradeDate가
오늘(KST)인지로 판단한다 → 공휴일·대체휴일·임시휴장을 전부 자동으로 걸러낸다.

- tradeDate == 기준 거래일     → 진행
- tradeDate가 다른 날짜(8자리) → skip (휴장일·주말)
- Daum 조회 실패·형식 이상     → 진행 (fail-open: 거래일을 통째로 놓치는 것보다 낫고,
                                 휴장일 중복은 클로드 발행 단계에서 한 번 더 걸러짐)

기준 거래일은 base_date()가 정한다 — 09:00 KST 이전이면 전날(사고 35).

stdout에는 GITHUB_OUTPUT 형식 두 줄("skip=...", "tradedate=YYYY-MM-DD")만 쓴다.
판단 이유는 stderr(Actions 로그)에 남긴다.
"""
import datetime as dt
import sys

import requests

KST = dt.timezone(dt.timedelta(hours=9))
URL = "https://finance.daum.net/api/quotes/A005930?summary=false"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.daum.net/quotes/A005930",
}


def base_date(now: dt.datetime) -> dt.date:
    """이 실행이 다뤄야 할 기준 거래일 후보.

    사고 35(2026-11-13 진단): GitHub schedule 이벤트가 5~10시간씩 지연돼
    실행 시각이 한국 자정을 넘기는 날이 잦다. 그때 '오늘(KST)'로 판정하면
    tradeDate(전날)와 영원히 어긋나 매일 '휴장일'이 되어 그날 글이 통째로
    사라진다(11-09~11-12 4거래일 누락).
    한국 증시는 09:00에 열리므로, 09:00 이전이라면 '가장 최근에 끝난 장'은
    전날이다. 이 규칙으로 자정을 넘긴 지연 실행도 제 거래일분을 처리한다.

    주말 조기 반환은 두지 않는다. 토요일 00:30(=금요일분 지연 도착)은
    base가 금요일이라 통과하고, 토요일 10:00은 base가 토요일이라
    tradeDate(금)와 어긋나 자연히 skip된다.
    """
    return now.date() if now.hour >= 9 else (now - dt.timedelta(days=1)).date()


def decide(base: dt.date, trade_date: str | None, error: str | None = None):
    """(skip, 이유) 반환. 네트워크 없이 테스트할 수 있게 판단만 분리."""
    if error is not None:
        return False, f"Daum 조회 실패 → 진행(fail-open): {error}"
    td = str(trade_date or "")
    ymd = base.strftime("%Y%m%d")
    if td == ymd:
        return False, f"거래일 확인 (tradeDate={td})"
    if len(td) == 8 and td.isdigit():
        return True, f"휴장일 — 최신 거래일 {td} ≠ 기준일 {ymd}"
    return False, f"tradeDate 형식 이상({td!r}) → 진행(fail-open)"


def main():
    now = dt.datetime.now(KST)
    base = base_date(now)
    trade_date, error = None, None
    try:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        trade_date = r.json().get("tradeDate")
    except Exception as e:  # noqa: BLE001
        error = repr(e)
    skip, why = decide(base, trade_date, error)
    td = str(trade_date or "")
    if not skip and len(td) == 8 and td.isdigit():
        use = f"{td[:4]}-{td[4:6]}-{td[6:]}"
    else:
        use = base.isoformat()
    print(f"skip={'true' if skip else 'false'}")
    print(f"tradedate={use}")
    print(f"[guard] now={now.isoformat()} base={base.isoformat()} "
          f"skip={skip} tradedate={use} — {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
