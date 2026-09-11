#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
차트로 보는 오늘 — 일일 데이터 수집기 (GitHub Actions에서 실행)

하는 일
  1) 당일 국내 전 종목 시세를 받아 거래대금·등락률 순위를 만든다
  2) 규격서 기준으로 핫종목 4~5개를 자동 선정한다
  3) 선정 종목의 2년치 일봉을 data/<코드>.csv 로 저장한다
  4) 시장 요약을 out/market.json 으로 저장한다

데이터 경로:
  - 스냅샷: 네이버 새 API(m.stock.naver.com) 우선, 실패 시 pykrx
  - 일봉: 야후 파이낸스 우선, 실패 시 pykrx
"""
from __future__ import annotations
import io, json, os, re, sys, time, traceback
import datetime as dt
import pandas as pd, numpy as np
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA, OUT = f"{ROOT}/data", f"{ROOT}/out"
os.makedirs(DATA, exist_ok=True); os.makedirs(OUT, exist_ok=True)

KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}

# 선정 파라미터
N_LARGE      = 1       # 대형주 최대 편수
N_TOTAL      = 5       # 총 선정 종목 수
LARGE_CAP_KR = 10_000_000_000_000   # 대형주 기준 시가총액 10조
MIN_VALUE    = 30_000_000_000       # 최소 거래대금 300억 (유동성 필터)
MIN_HISTORY  = 300     # 240일선을 그리려면 최소 300거래일


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


# ────────────────────────────────────────────────────────────
# 1. 당일 전 종목 스냅샷 — 네이버 새 API
# ────────────────────────────────────────────────────────────
NAVER_API = "https://m.stock.naver.com/api/json/sise/siseListJson.nhn"

def _naver_fetch(menu: str, sosok: int, page_size: int = 100) -> list[dict]:
    """네이버 siseListJson 한 페이지 호출 → itemList 반환"""
    url = f"{NAVER_API}?menu={menu}&sosok={sosok}&pageSize={page_size}&page=1"
    r = requests.get(url, headers=UA, timeout=20)
    j = r.json()
    if j.get("resultCode") != "success":
        raise RuntimeError(f"naver api error: {menu} sosok={sosok}")
    return j["result"]["itemList"]


def _parse_items(items: list[dict], market: str) -> list[dict]:
    """API item → 표준 딕셔너리로 변환 + ETF/ETN 필터"""
    out = []
    for it in items:
        if it.get("etf") or it.get("etn"):
            continue
        try:
            code = str(it["cd"]).zfill(6)
            name = str(it["nm"]).strip()
            close = float(it["nv"])
            chg = float(it["cr"])
            volume = float(it.get("aq", 0))
            value = float(it.get("aq", 0)) * float(it.get("nv", 0))  # 거래량 × 종가
            mcap = float(it.get("mks", 0)) * 100_000_000     # 억원 → 원
            out.append({
                "code": code, "name": name, "market": market,
                "close": close, "chg": chg,
                "volume": volume, "value": value, "mcap": mcap,
            })
        except Exception:
            continue
    return out


def snapshot_naver() -> pd.DataFrame:
    """네이버 새 API로 KOSPI·KOSDAQ × 시총/거래대금/상승 6개 페이지 합침"""
    combos = [
        ("market_sum", 0, "KOSPI"),
        ("market_sum", 1, "KOSDAQ"),
        ("quant",      0, "KOSPI"),
        ("quant",      1, "KOSDAQ"),
        ("rise",       0, "KOSPI"),
        ("rise",       1, "KOSDAQ"),
    ]
    rows = []
    for menu, sosok, mkt in combos:
        try:
            items = _naver_fetch(menu, sosok)
            rows.extend(_parse_items(items, mkt))
        except Exception as e:
            print(f"[warn] naver {menu} sosok={sosok}: {e}", file=sys.stderr)
    if not rows:
        raise RuntimeError("네이버 API를 모두 받지 못했습니다")
    df = pd.DataFrame(rows)
    df = df.drop_duplicates("code")
    print(f"[ok] naver 스냅샷 {len(df)}종목 (ETF/ETN 제외)")
    return df


def snapshot_pykrx(day: str) -> pd.DataFrame:
    from pykrx import stock
    ohlcv = stock.get_market_ohlcv(day, market="ALL")
    cap = stock.get_market_cap(day, market="ALL")
    df = ohlcv.join(cap[["시가총액"]], how="inner")
    df = df.rename(columns={"종가": "close", "등락률": "chg", "거래대금": "value",
                            "거래량": "volume", "시가총액": "mcap"})
    df["name"] = [stock.get_market_ticker_name(t) for t in df.index]
    df["market"] = ["KOSPI" if t in set(stock.get_market_ticker_list(day, "KOSPI"))
                    else "KOSDAQ" for t in df.index]
    df.index.name = "code"
    return df.reset_index()[["code", "name", "market", "close", "chg", "volume", "value", "mcap"]]


def market_snapshot(day: str):
    # 네이버 우선 (pykrx는 최근 KRX 로그인 요구로 자주 실패)
    try:
        df = snapshot_naver()
        return df, "naver"
    except Exception:
        traceback.print_exc()
        print("[warn] naver 실패 → pykrx로 대체", file=sys.stderr)
        df = snapshot_pykrx(day)
        print(f"[ok] pykrx 스냅샷 {len(df)}종목")
        return df, "pykrx"


# ────────────────────────────────────────────────────────────
# 2. 종목 선정
# ────────────────────────────────────────────────────────────
def pick(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["value"]).copy()
    d = d[d["value"] >= MIN_VALUE]
    # 우선주·스팩·ETF/ETN 추가 필터 (이중 안전장치)
    bad = d["name"].str.contains(r"우$|우[ABC]$|스팩|제\d+호|KODEX|TIGER|KBSTAR|ARIRANG|"
                                 r"ETN|레버리지|인버스|선물", regex=True, na=False)
    d = d[~bad]

    large = d[d["mcap"].fillna(0) >= LARGE_CAP_KR].sort_values("value", ascending=False)
    small = d[d["mcap"].fillna(0) < LARGE_CAP_KR].sort_values("chg", ascending=False)

    chosen = pd.concat([large.head(N_LARGE), small.head(N_TOTAL - N_LARGE)])
    return chosen.reset_index(drop=True)


# ────────────────────────────────────────────────────────────
# 3. 일봉 수집 (야후 우선)
# ────────────────────────────────────────────────────────────
def daily_yahoo(code: str, market: str | None) -> pd.DataFrame:
    last = None
    for suf in ([".KS", ".KQ"] if not market else
                ([".KS"] if market == "KOSPI" else [".KQ"])):
        try:
            u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suf}"
                 f"?range=2y&interval=1d")
            j = requests.get(u, headers=UA, timeout=30).json()
            R = j["chart"]["result"][0]; q = R["indicators"]["quote"][0]
            df = pd.DataFrame({
                "date": pd.to_datetime(R["timestamp"], unit="s", utc=True)
                          .tz_convert("Asia/Seoul").normalize().tz_localize(None),
                "open": q["open"], "high": q["high"], "low": q["low"],
                "close": q["close"], "volume": q["volume"]})
            df = df.dropna(subset=["close"])
            if len(df) > MIN_HISTORY // 2:
                return df
        except Exception as e:
            last = e
    raise RuntimeError(f"야후 일봉 실패 {code}: {last}")


def daily_pykrx(code: str, start: str, end: str) -> pd.DataFrame:
    from pykrx import stock
    df = stock.get_market_ohlcv(start, end, code)
    df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                            "종가": "close", "거래량": "volume"})
    df.index.name = "date"
    return df.reset_index()[["date", "open", "high", "low", "close", "volume"]]


def daily(code: str, market: str | None, day: dt.date) -> pd.DataFrame:
    # 야후 우선 (pykrx는 KRX 로그인 이슈)
    try:
        return daily_yahoo(code, market)
    except Exception as e:
        print(f"[warn] {code} 야후 실패({e}) → pykrx", file=sys.stderr)
    start = (day - dt.timedelta(days=760)).strftime("%Y%m%d")
    return daily_pykrx(code, start, day.strftime("%Y%m%d"))


# ────────────────────────────────────────────────────────────
# 4. 품질 검사
# ────────────────────────────────────────────────────────────
def quality_problem(df: pd.DataFrame) -> str | None:
    if len(df) < MIN_HISTORY:
        return f"거래일 {len(df)}일뿐 — 240일선을 그릴 수 없음(신규상장 등)"
    c = df["close"].tail(120)
    flat = (c.pct_change().abs() < 0.002).rolling(15).sum().max()
    if flat is not None and flat >= 14:
        return "최근 120일 중 15거래일 이상 주가가 사실상 고정 — 기업 이벤트로 이평선 왜곡"
    if (df["volume"].tail(60) == 0).sum() >= 3:
        return "최근 60일에 거래량 0인 날이 3일 이상 — 데이터 신뢰 불가"
    return None


# ────────────────────────────────────────────────────────────
def main():
    day = today_kst()
    if len(sys.argv) > 1:
        day = dt.date.fromisoformat(sys.argv[1])
    ymd = day.strftime("%Y%m%d")

    snap, source = market_snapshot(ymd)
    snap.to_csv(f"{OUT}/snapshot.csv", index=False)

    cands = pick(snap)
    print("\n[선정 후보]\n", cands[["code", "name", "chg", "value", "mcap"]].to_string())

    picked, rejected = [], []
    for _, r in cands.iterrows():
        code = r["code"]
        if not code or (isinstance(code, float) and np.isnan(code)):
            rejected.append({"name": r["name"], "reason": "종목코드 확인 불가"}); continue
        code = str(code).zfill(6)
        try:
            df = daily(code, r.get("market"), day)
        except Exception as e:
            rejected.append({"code": code, "name": r["name"], "reason": f"일봉 수집 실패: {e}"})
            continue
        why = quality_problem(df)
        if why:
            rejected.append({"code": code, "name": r["name"], "reason": why}); continue
        df.to_csv(f"{DATA}/{code}.csv", index=False, date_format="%Y-%m-%d")
        picked.append({"code": code, "name": r["name"],
                       "market": r.get("market"), "chg": float(r["chg"]),
                       "value": None if pd.isna(r.get("value")) else float(r["value"]),
                       "mcap": None if pd.isna(r.get("mcap")) else float(r["mcap"])})
        print(f"[ok] {code} {r['name']} {len(df)}행 저장")

    # watchlist 갱신
    watch = []
    wf = f"{ROOT}/watchlist.json"
    if os.path.exists(wf):
        for w in json.load(open(wf)):
            try:
                df = daily(w["code"], w.get("market"), day)
                df.to_csv(f"{DATA}/{w['code']}.csv", index=False, date_format="%Y-%m-%d")
                watch.append({**w, "last_close": int(df["close"].iloc[-1]),
                              "last_date": str(df["date"].iloc[-1])[:10]})
            except Exception as e:
                print(f"[warn] watchlist {w['code']}: {e}", file=sys.stderr)

    out = {"date": day.isoformat(), "source": source,
           "picked": picked, "rejected": rejected, "watchlist": watch,
           "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    json.dump(out, open(f"{OUT}/market.json", "w"), ensure_ascii=False, indent=1)
    print("\n[완료]", json.dumps({"picked": len(picked), "rejected": len(rejected)},
                                ensure_ascii=False))
    if not picked:
        sys.exit("선정된 종목이 없습니다 — 데이터 경로를 점검하세요")


if __name__ == "__main__":
    main()
