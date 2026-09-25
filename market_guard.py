#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""휴장일 판별 — 사고 15·16(2026-09-24·25 추석 연휴) 대응

휴장일에도 파이프라인이 돌아서 전 거래일 데이터로 '새 원고'를 다시 만들던 문제를 막는다.
휴장일 목록을 따로 관리하지 않고, Daum 삼성전자(A005930) 시세의 tradeDate가
오늘(KST)인지로 판단한다 → 공휴일·대체휴일·임시휴장을 전부 자동으로 걸러낸다.

- 주말                       → skip
- tradeDate == 오늘           → 진행
- tradeDate가 다른 날짜(8자리) → skip (휴장일)
- Daum 조회 실패·형식 이상     → 진행 (fail-open: 거래일을 통째로 놓치는 것보다 낫고,
                                 휴장일 중복은 클로드 발행 단계에서 한 번 더 걸러짐)

stdout에는 GITHUB_OUTPUT 형식 한 줄("skip=true" 또는 "skip=false")만 쓴다.
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


def decide(today: dt.date, trade_date: str | None, error: str | None = None):
    """(skip, 이유) 반환. 네트워크 없이 테스트할 수 있게 판단만 분리."""
    if today.isoweekday() >= 6:
        return True, "주말"
    if error is not None:
        return False, f"Daum 조회 실패 → 진행(fail-open): {error}"
    td = str(trade_date or "")
    ymd = today.strftime("%Y%m%d")
    if td == ymd:
        return False, f"거래일 확인 (tradeDate={td})"
    if len(td) == 8 and td.isdigit():
        return True, f"휴장일 — 최신 거래일 {td} ≠ 오늘 {ymd}"
    return False, f"tradeDate 형식 이상({td!r}) → 진행(fail-open)"


def main():
    today = dt.datetime.now(KST).date()
    trade_date, error = None, None
    if today.isoweekday() < 6:
        try:
            r = requests.get(URL, headers=HEADERS, timeout=15)
            r.raise_for_status()
            trade_date = r.json().get("tradeDate")
        except Exception as e:  # noqa: BLE001
            error = repr(e)
    skip, why = decide(today, trade_date, error)
    print(f"skip={'true' if skip else 'false'}")
    print(f"[guard] {today.isoformat()} skip={skip} — {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
