#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
차트로 보는 오늘 — 일일 데이터 수집기 (GitHub Actions에서 실행)

하는 일
  1) 당일 국내 전 종목 시세를 받아 거래대금·등락률 순위를 만든다
  2) 규격서 기준으로 핫종목 4~5개를 자동 선정한다
  3) 선정 종목의 2년치 일봉을 data/<코드>.csv 로 저장한다
  4) 시장 요약을 out/market.json 으로 저장한다

데이터 경로는 2중화: pykrx(KRX 직접) → 실패 시 네이버 금융.
GitHub Actions 러너는 해외 IP라 어느 한쪽이 막힐 수 있어 반드시 두 경로를 둔다.
"""
from __future__ import annotations
import io, json, os, sys, time, traceback
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


# ───────────────────────────────────────────────────────────
# 1. 당일 전 종목 스냅샷
# ────────────────────────────────────────────────────────────
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


def _naver_table(url: str) -> pd.DataFrame:
    r = requests.get(url, headers=UA, timeout=20)
    r.encoding = "euc-kr"
    tables = pd.read_html(io.StringIO(r.text))
    df = max(tables, key=len).dropna(how="all").dropna(axis=1, how="all")
    return df


def snapshot_naver() -> pd.DataFrame:
    """네이버 거래대금/상승률 상위 페이지를 합쳐 후보군을 만든다 (전 종목은 아님)"""
    frames = []
    for url in ("https://finance.naver.com/sise/sise_quant.naver",
                "https://finance.naver.com/sise/sise_rise.naver",
                "https://finance.naver.com/sise/sise_quant.naver?sosok=1",
                "https://finance.naver.com/sise/sise_rise.naver?sosok=1"):
        try:
            frames.append(_naver_table(url))
        except Exception as e:
            print(f"[warn] naver {url}: {e}", file=sys.stderr)
    if not frames:
        raise RuntimeError("네이버 순위 페이지를 모두 받지 못했습니다")
    df = pd.concat(frames, ignore_index=True)
    ren = {"종목명": "name", "현재가": "close", "등락률": "chg",
           "거래량": "volume", "거래대금": "value", "시가총액": "mcap"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    df = df[[c for c in ("name", "close", "chg", "volume", "value", "mcap") if c in df.columns]]
    df = df.dropna(subset=["name"]).drop_duplicates("name")
    for c in ("close", "volume", "value", "mcap"):
        if c in df: df[c] = pd.to_numeric(df[c].astype(str).str.replace(r"[^\d.-]", "", regex=True),
                                          errors="coerce")
    df["chg"] = pd.to_numeric(df["chg"].astype(str).str.replace("%", "").str.replace("+", ""),
                              errors="coerce")
    if "value" in df: df["value"] = df["value"] * 1_000_000     # 네이버 거래대금 단위: 백만원
    if "mcap" in df:  df["mcap"] = df["mcap"] * 100_000_000     # 시가총액 단위: 억원
    df["code"] = None; df["market"] = None
    return df


def market_snapshot(day: str):
    try:
        df = snapshot_pykrx(day)
        print(f"[ok] pykrx 스냅샷 {len(df)}종목")
        return df, "pykrx"
    except Exception:
        traceback.print_exc()
        print("[warn] pykrx 실패 → 네이버로 대체", file=sys.stderr)
        df = snapshot_naver()
        print(f"[ok] naver 스냅샷 {len(df)}종목")
        return df, "naver"


# ────────────────────────────────────────────────────────────
# 2. 종목 선정
# ────────────────────────────────────────────────────────────
def pick(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["value"]).copy()
    d = d[d["value"] >= MIN_VALUE]
    # 우선주·스팩·ETF/ETN 제외
    bad = d["name"].str.contains(r"우$|우[ABC]$|스팩|제\d+호|KODEX|TIGER|KBSTAR|ARIRANG|"
                                 r"ETN|레버리지|인버스|선물", regex=True, na=False)
    d = d[~bad]

    large = d[d["mcap"].fillna(0) >= LARGE_CAP_KR].sort_values("value", ascending=False)
    small = d[d["mcap"].fillna(0) < LARGE_CAP_KR].sort_values("chg", ascending=False)

    chosen = pd.concat([large.head(N_LARGE), small.head(N_TOTAL - N_LARGE)])
    return chosen.reset_index(drop=True)


# ────────────────────────────────────────────────────────────
# 3. 일봉 수집
# ────────────────────────────────────────────────────────────
def daily_pykrx(code: str, start: str, end: str) -> pd.DataFrame:
    from pykrx import stock
    df = stock.get_market_ohlcv(start, end, code)
    df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                            "종가": "close", "거래량": "volume"})
    df.index.name = "date"
    return df.reset_index()[["date", "open", "high", "low", "close", "volume"]]


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


def daily(code: str, market: str | None, day: dt.date) -> pd.DataFrame:
    start = (day - dt.timedelta(days=760)).strftime("%Y%m%d")
    try:
        df = daily_pykrx(code, start, day.strftime("%Y%m%d"))
        if len(df) >= MIN_HISTORY: return df
        print(f"[warn] {code} pykrx 이력 {len(df)}행 → 야후로 재시도", file=sys.stderr)
    except Exception as e:
        print(f"[warn] {code} pykrx 실패({e}) → 야후", file=sys.stderr)
    return daily_yahoo(code, market)


# ────────────────────────────────────────────────────────────
# 4. 품질 검사 — 기술적 분석이 성립하지 않는 종목 걸러내기
# ────────────────────────────────────────────────────────────
def quality_problem(df: pd.DataFrame) -> str | None:
    if len(df) < MIN_HISTORY:
        return f"거래일 {len(df)}일뿐 — 240일선을 그릴 수 없음(신규상장 등)"
    c = df["close"].tail(120)
    # 공개매수 등으로 가격이 장기간 고정된 구간 탐지
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

    # 이미 소개한 종목(누적 수익률용)도 계속 갱신
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
