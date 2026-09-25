#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
차트로 보는 오늘 — 일일 데이터 수집기

데이터 경로:
  - 스냅샷: 네이버 새 API
  - 일봉: 네이버 일봉 API 우선 → 야후 → pykrx
  - 뉴스: 네이버페이증권 종목 뉴스 API (picked 종목만)

종목 선정: 『언덕을 넘을 때 산다』 매수 전 체크리스트 기반 필터
"""
from __future__ import annotations
import ast, io, json, os, re, sys, time, traceback
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

N_LARGE      = 1
N_TOTAL      = 5
LARGE_CAP_KR = 10_000_000_000_000
MIN_VALUE    = 30_000_000_000
MIN_HISTORY  = 300


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def add_indicators(df):
    c = df["close"]
    h, l = df["high"], df["low"]
    for n in (5, 10, 20, 240):
        df[f"ma{n}"] = c.rolling(n).mean()
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    df["tenkan"] = tenkan
    df["kijun"] = kijun
    return df


# ────────────────────────────────────────────────────────────
# 스냅샷 — 네이버 API
# ────────────────────────────────────────────────────────────
NAVER_API = "https://m.stock.naver.com/api/json/sise/siseListJson.nhn"


def _naver_fetch(menu: str, sosok: int, page_size: int = 100) -> list[dict]:
    url = f"{NAVER_API}?menu={menu}&sosok={sosok}&pageSize={page_size}&page=1"
    r = requests.get(url, headers=UA, timeout=20)
    j = r.json()
    if j.get("resultCode") != "success":
        raise RuntimeError(f"naver api error: {menu} sosok={sosok}")
    return j["result"]["itemList"]


def _parse_items(items: list[dict], market: str) -> list[dict]:
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
            value = float(it.get("aq", 0)) * float(it.get("nv", 0))
            mcap = float(it.get("mks", 0)) * 100_000_000
            out.append({
                "code": code, "name": name, "market": market,
                "close": close, "chg": chg,
                "volume": volume, "value": value, "mcap": mcap,
            })
        except Exception:
            continue
    return out


def snapshot_naver() -> pd.DataFrame:
    combos = [
        ("market_sum", 0, "KOSPI"), ("market_sum", 1, "KOSDAQ"),
        ("quant",      0, "KOSPI"), ("quant",      1, "KOSDAQ"),
        ("rise",       0, "KOSPI"), ("rise",       1, "KOSDAQ"),
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
    df = pd.DataFrame(rows).drop_duplicates("code")
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
    """스냅샷: 날짜별 캐시로 재현성 보장.
    - 오늘: 네이버 실시간 → 캐시 저장
    - 과거: 캐시 있으면 재사용, 없으면 pykrx
    - 과거 재실행에 네이버 실시간을 쓰지 않는다 (picked 흔들림 방지)
    """
    cache_path = f"{OUT}/snapshot_{day}.csv"
    today_str = today_kst().strftime("%Y%m%d")

    # 1) 과거 날짜 & 캐시 존재 → 재사용
    if day != today_str and os.path.exists(cache_path):
        df = pd.read_csv(cache_path, dtype={"code": str})
        df["code"] = df["code"].str.zfill(6)
        print(f"[ok] 스냅샷 캐시 재사용 {cache_path} ({len(df)}종목)")
        return df, "cache"

    # 2) 오늘 → 네이버 실시간
    if day == today_str:
        try:
            df = snapshot_naver()
            df.to_csv(cache_path, index=False)
            print(f"[ok] 스냅샷 캐시 저장 {cache_path}")
            return df, "naver"
        except Exception:
            traceback.print_exc()
            print("[warn] naver 실패 → pykrx로 대체", file=sys.stderr)

    # 3) 과거 & 캐시 없음 → pykrx로 해당 날짜 조회 (네이버 실시간 금지)
    df = snapshot_pykrx(day)
    df.to_csv(cache_path, index=False)
    print(f"[ok] pykrx 스냅샷 {len(df)}종목 (과거 {day})")
    return df, "pykrx"


# ────────────────────────────────────────────────────────────
# 일봉 수집
# ────────────────────────────────────────────────────────────
def daily_naver(code: str, day: dt.date) -> pd.DataFrame:
    start = (day - dt.timedelta(days=760)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")
    url = (f"https://api.finance.naver.com/siseJson.naver?"
           f"symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe=day")
    r = requests.get(url, headers=UA, timeout=30)
    data = ast.literal_eval(r.text.strip())
    if len(data) < 2:
        raise RuntimeError(f"네이버 일봉 데이터 없음: {code}")
    df = pd.DataFrame(data[1:],
                      columns=["date", "open", "high", "low", "close", "volume", "foreign"])
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    if len(df) < MIN_HISTORY // 2:
        raise RuntimeError(f"네이버 일봉 데이터 부족: {code} {len(df)}행")
    return df


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
    try:
        return daily_naver(code, day)
    except Exception as e:
        print(f"[warn] {code} 네이버 일봉 실패({e}) → 야후", file=sys.stderr)
    try:
        return daily_yahoo(code, market)
    except Exception as e:
        print(f"[warn] {code} 야후 실패({e}) → pykrx", file=sys.stderr)
    start = (day - dt.timedelta(days=760)).strftime("%Y%m%d")
    return daily_pykrx(code, start, day.strftime("%Y%m%d"))


# ────────────────────────────────────────────────────────────
# 오늘 종가 보정 — Daum 정규장 종가 (사고 14)
# 네이버 일봉의 마지막 날 종가는 21시 수집 시점에 시간외 단일가까지 반영돼 있어
# 정규장 종가와 다를 수 있다(09-23 실측: 삼성전자 286,500 vs 정규장 285,500).
# 이 값이 data/*.csv → 지표·차트, watchlist.json last_close/intro_close 로 그대로 흘러가므로
# 마지막 행 날짜가 Daum tradeDate 와 같을 때만 종가를 regularTradePrice 로 바꾼다.
# Daum 조회가 실패하면 기존 값을 그대로 둔다(파이프라인은 멈추지 않음).
# ────────────────────────────────────────────────────────────
_DAUM_CACHE: dict = {}


def daum_regular_close(code: str):
    """(YYYY-MM-DD, 정규장 종가) 또는 실패 시 None."""
    if code in _DAUM_CACHE:
        return _DAUM_CACHE[code]
    res = None
    try:
        r = requests.get(f"https://finance.daum.net/api/quotes/A{code}?summary=false",
                         headers={**UA, "Referer": f"https://finance.daum.net/quotes/A{code}"},
                         timeout=10)
        j = r.json()
        td = str(j["tradeDate"])
        px = int(j["regularTradePrice"])
        if len(td) == 8 and td.isdigit() and px > 0:
            res = (f"{td[:4]}-{td[4:6]}-{td[6:]}", px)
        time.sleep(0.3)
    except Exception as e:
        print(f"[warn] Daum 종가 조회 실패 {code}: {e}", file=sys.stderr)
    _DAUM_CACHE[code] = res
    return res


def fix_last_close(code: str, df: pd.DataFrame) -> pd.DataFrame:
    """df 마지막 행이 Daum 최신 거래일과 같은 날이면 종가를 정규장 종가로 교체."""
    if df is None or not len(df):
        return df
    q = daum_regular_close(code)
    if not q:
        return df
    d, px = q
    last_d = str(df["date"].iloc[-1])[:10]
    if last_d != d:
        return df
    old = df["close"].iloc[-1]
    if pd.isna(old) or int(round(float(old))) != px:
        df = df.copy()
        df.loc[df.index[-1], "close"] = px
        print(f"[fix] {code} {d} 종가 {old} → {px:,} (Daum 정규장 종가)")
    return df


# ────────────────────────────────────────────────────────────
# 뉴스 수집 — 네이버페이증권 종목 뉴스 API
# ────────────────────────────────────────────────────────────
def fetch_news(code: str, page_size: int = 5, max_age_days: int = 3) -> list[dict]:
    """종목 뉴스 (최근 max_age_days 이내, 최신순)"""
    url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={page_size}&page=1"
    try:
        r = requests.get(url, headers=UA, timeout=10)
        j = r.json()
        cutoff = dt.datetime.now(KST) - dt.timedelta(days=max_age_days)
        seen, news = set(), []
        for section in j:
            for it in section.get("items", []):
                nid = it.get("id")
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                raw_dt = it.get("datetime", "")
                try:
                    ndt = dt.datetime.strptime(raw_dt, "%Y%m%d%H%M").replace(tzinfo=KST)
                except Exception:
                    ndt = None
                if ndt and ndt < cutoff:
                    continue
                news.append({
                    "title": it.get("title", "").strip(),
                    "office": it.get("officeName", "").strip(),
                    "datetime": raw_dt,
                    "url": it.get("mobileNewsUrl", "").strip(),
                })
        news.sort(key=lambda x: x["datetime"], reverse=True)
        return news[:page_size]
    except Exception as e:
        print(f"[warn] news {code}: {e}", file=sys.stderr)
        return []


# ────────────────────────────────────────────────────────────
# 책 이론 필터 + 종목 선정
# ────────────────────────────────────────────────────────────
def check_book_filter(df_d: pd.DataFrame, asof_idx: int = -1):
    n = len(df_d)
    if asof_idx < 0:
        asof_idx = n + asof_idx
    if asof_idx < 0 or asof_idx >= n or asof_idx < 240:
        return False, None
    row = df_d.iloc[asof_idx]
    if pd.isna(row["ma240"]) or pd.isna(row["kijun"]) or pd.isna(row["ma20"]):
        return False, None
    if row["close"] <= row["ma240"]: return False, None
    if row["close"] <= row["kijun"]: return False, None
    if not (row["ma5"] > row["ma10"] > row["ma20"]): return False, None
    vs_ma240 = (row["close"] / row["ma240"] - 1) * 100
    if vs_ma240 > 50: return False, None
    if row["close"] < row["ma5"]: return False, None
    return True, row


def pick(df: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    d = df.dropna(subset=["value"]).copy()
    d = d[d["value"] >= MIN_VALUE]
    bad = d["name"].str.contains(r"우$|우[ABC]$|스팩|제\d+호|KODEX|TIGER|KBSTAR|ARIRANG|"
                                 r"ETN|레버리지|인버스|선물", regex=True, na=False)
    d = d[~bad]

    large = d[d["mcap"].fillna(0) >= LARGE_CAP_KR].sort_values("value", ascending=False)
    mid = d[(d["mcap"].fillna(0) < LARGE_CAP_KR) & (d["chg"] >= -10) & (d["chg"] <= 20)]
    small = mid.sort_values("value", ascending=False)

    cand = pd.concat([
        large.head(N_LARGE * 8),
        small.head((N_TOTAL - N_LARGE) * 15),
    ]).drop_duplicates("code").reset_index(drop=True)

    passed, already, cached = [], set(), {}
    for _, r in cand.iterrows():
        code = str(r["code"]).zfill(6)
        try:
            df_d = add_indicators(daily(code, r.get("market"), day))
            cached[code] = df_d
            ok, _ = check_book_filter(df_d, -1)
            if ok:
                passed.append(r); already.add(code)
        except Exception as e:
            print(f"[warn] pick {r['name']}: {e}", file=sys.stderr)
            continue
        if len(passed) >= N_TOTAL:
            break

    print(f"[ok] 1차 필터 통과: {len(passed)}종목")

    if len(passed) < N_TOTAL:
        needed = N_TOTAL - len(passed)
        print(f"[info] {needed}종목 부족 → 눌림 관찰 대상 보충 시도")
        for _, r in d.sort_values("value", ascending=False).head(50).iterrows():
            if len(passed) >= N_TOTAL: break
            code = str(r["code"]).zfill(6)
            if code in already: continue
            try:
                df_d = cached.get(code) or add_indicators(daily(code, r.get("market"), day))
                cached[code] = df_d
                if not (-10 <= r["chg"] <= 0): continue
                last = df_d.iloc[-1]
                if pd.isna(last["ma240"]) or last["close"] <= last["ma240"]: continue
                if not (last["ma5"] > last["ma10"] > last["ma20"]): continue
                for back in (1, 2):
                    ok, _ = check_book_filter(df_d, -1 - back)
                    if ok:
                        passed.append(r); already.add(code)
                        print(f"[info] 눌림 보충: {r['name']} ({back}일 전 통과, 오늘 {r['chg']:+.2f}%)")
                        break
            except Exception as e:
                print(f"[warn] fallback {r['name']}: {e}", file=sys.stderr)
                continue

    if not passed:
        print("[warn] 필터 통과 종목 없음", file=sys.stderr)
        return pd.DataFrame(columns=df.columns)
    return pd.DataFrame(passed).reset_index(drop=True)


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

    cands = pick(snap, day)
    if len(cands):
        print("\n[선정 후보]\n", cands[["code", "name", "chg", "value", "mcap"]].to_string())
    else:
        print("\n[선정 후보] 없음")

    picked, rejected = [], []
    for _, r in cands.iterrows():
        code = r["code"]
        if not code or (isinstance(code, float) and np.isnan(code)):
            rejected.append({"name": r["name"], "reason": "종목코드 확인 불가"}); continue
        code = str(code).zfill(6)
        try:
            df = fix_last_close(code, daily(code, r.get("market"), day))
        except Exception as e:
            rejected.append({"code": code, "name": r["name"], "reason": f"일봉 수집 실패: {e}"})
            continue
        why = quality_problem(df)
        if why:
            rejected.append({"code": code, "name": r["name"], "reason": why}); continue
        df.to_csv(f"{DATA}/{code}.csv", index=False, date_format="%Y-%m-%d")
        news = fetch_news(code)
        print(f"[ok] {code} {r['name']} {len(df)}행 / 뉴스 {len(news)}건")
        picked.append({"code": code, "name": r["name"],
                       "market": r.get("market"), "chg": float(r["chg"]),
                       "value": None if pd.isna(r.get("value")) else float(r["value"]),
                       "mcap": None if pd.isna(r.get("mcap")) else float(r["mcap"]),
                       "news": news})

    # ── watchlist 자동 갱신 ──────────────────────────────────
    watch = []
    wf = f"{ROOT}/watchlist.json"
    existing = json.load(open(wf)) if os.path.exists(wf) else []
    existing_codes = {w["code"] for w in existing}

    # 1) 기존 watchlist 항목: 최신 종가 갱신
    for w in existing:
        try:
            df = fix_last_close(w["code"], daily(w["code"], w.get("market"), day))
            df.to_csv(f"{DATA}/{w['code']}.csv", index=False, date_format="%Y-%m-%d")
            watch.append({**w, "last_close": int(df["close"].iloc[-1]),
                          "last_date": str(df["date"].iloc[-1])[:10]})
        except Exception as e:
            print(f"[warn] watchlist {w['code']}: {e}", file=sys.stderr)
            watch.append(w)  # 실패 시 기존 값 유지

    # 2) picked 중 watchlist에 없는 종목 → 신규 추가
    for p in picked:
        if p["code"] in existing_codes:
            continue
        try:
            df = fix_last_close(p["code"], daily(p["code"], p.get("market"), day))
            df.to_csv(f"{DATA}/{p['code']}.csv", index=False, date_format="%Y-%m-%d")
            new_entry = {
                "code": p["code"],
                "name": p["name"],
                "market": p.get("market"),
                "intro_date": day.isoformat(),
                "intro_close": int(df["close"].iloc[-1]),
                "last_close": int(df["close"].iloc[-1]),
                "last_date": str(df["date"].iloc[-1])[:10],
            }
            watch.append(new_entry)
            print(f"[신규] watchlist 추가: {p['name']} ({p['code']})")
        except Exception as e:
            print(f"[warn] watchlist 신규 {p['code']}: {e}", file=sys.stderr)

    # 3) watchlist.json 저장
    json.dump(watch, open(wf, "w"), ensure_ascii=False, indent=1)


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
