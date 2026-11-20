#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py — 「차트로 보는 오늘」 원고 자동 수치 검증기

generate_articles.py 가 원고를 만든 뒤 실행합니다.
사람(클로드)이 매일 손으로 하던 숫자 대조를 전부 자동으로 돌리고
결과를 out/verify/<YYYYMMDD>.json 에 씁니다.

검사 항목
  A. Daum 정규장 값과 analysis JSON 대조
     (종가·시가·고가·저가·전일종가·등락률·거래량·52주 고저·상장일)
     → 사고 22 / 24 / 33 계열
  B. data/<코드>.csv 로 MA(5·10·20·60·120·240)·RSI(Wilder)·MACD·일목 재계산
     → analysis 와 대조하고, analysis 에 없는 ma60·ma120 을 산출
  C. 「정배열」 서술 범위 검사 — 전 구간이 정렬돼 있지 않은데
     범위 한정 없이 '정배열' 이라고 썼는지
  D. 캔들 4값 관계 서술 검사 (윗꼬리/아랫꼬리/양봉/음봉) → 사고 37
  E. 원고에 등장하는 모든 수치를 '알려진 참값 집합' 과 대조해
     출처를 못 찾은 숫자를 뽑아냄 → 사고 24 가 여기서도 걸림
  F. 차트 PNG 접근성(status·bytes) 확인

종료 코드
  0  오류 없음 (경고는 있을 수 있음)
  1  errors 발생 — 사람이 봐야 함
  2  실행 자체 실패 (입력 없음 등)

사용법
  python3 verify.py --date 20261120
  python3 verify.py --date 20261120 --daum-cache daum.json   # 오프라인 테스트
  python3 verify.py --date 20261120 --root . --out out/verify
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import unicodedata

try:
    import urllib.request
    import urllib.error
except Exception:  # pragma: no cover
    urllib = None

DAUM_QUOTE = "https://finance.daum.net/api/quotes/A{code}"
DAUM_HEADERS = {
    "referer": "https://finance.daum.net/domestic/stocks/",
    "user-agent": "Mozilla/5.0",
}

# ─────────────────────────────── 유틸 ───────────────────────────────


def _num(s):
    """'213,000' -> 213000.0 / '-0.93' -> -0.93 / 실패하면 None"""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = s.replace(",", "").replace("−", "-").replace("−", "-")
    try:
        return float(s)
    except ValueError:
        return None


def _close(a, b, tol):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _pct_close(a, b, tol_pct):
    """상대 오차 비교. b 가 0 이면 절대 비교."""
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a) <= tol_pct
    return abs(a - b) / abs(b) * 100.0 <= tol_pct


# ───────────────────────────── 지표 재계산 ─────────────────────────────


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append(
                    {
                        "date": r["date"],
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                        "volume": float(r["volume"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals, n):
    if not vals:
        return []
    k = 2.0 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi_wilder(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def recompute(rows):
    """CSV 전체로 지표를 다시 계산한다."""
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]

    out = {}
    for n in (5, 10, 20, 60, 120, 240):
        out["ma%d" % n] = sma(closes, n)

    out["rsi"] = rsi_wilder(closes)

    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    if len(macd_line) > 25:
        sig = ema_series(macd_line[25:], 9)
        out["macd"] = macd_line[-1]
        out["macd_sig"] = sig[-1]
        out["macd_hist"] = macd_line[-1] - sig[-1]
    else:
        out["macd"] = out["macd_sig"] = out["macd_hist"] = None

    if len(rows) >= 26:
        out["tenkan"] = (max(highs[-9:]) + min(lows[-9:])) / 2.0
        out["kijun"] = (max(highs[-26:]) + min(lows[-26:])) / 2.0
    else:
        out["tenkan"] = out["kijun"] = None

    for win, label in ((60, "60"), (120, "120"), (240, "240")):
        if len(rows) >= win:
            out["recent%s_high" % label] = max(highs[-win:])
            out["recent%s_low" % label] = min(lows[-win:])

    # 진짜 52주(365일) 창 — build.py 의 240거래일 창과 다르다 (사고 24)
    try:
        end = dt.date.fromisoformat(rows[-1]["date"])
        start = end - dt.timedelta(days=365)
        win = [r for r in rows if dt.date.fromisoformat(r["date"]) >= start]
        if win:
            out["high_52w_calendar"] = max(r["high"] for r in win)
            out["low_52w_calendar"] = min(r["low"] for r in win)
            out["high_52w_calendar_date"] = max(win, key=lambda r: r["high"])["date"]
            out["low_52w_calendar_date"] = min(win, key=lambda r: r["low"])["date"]
    except Exception:
        pass

    if len(rows) >= 21:
        out["vol_ma20_prev"] = sum(r["volume"] for r in rows[-21:-1]) / 20.0
        if out["vol_ma20_prev"]:
            out["vol_ratio"] = rows[-1]["volume"] / out["vol_ma20_prev"]

    out["open"] = rows[-1]["open"]
    out["high"] = rows[-1]["high"]
    out["low"] = rows[-1]["low"]
    out["close"] = rows[-1]["close"]
    out["volume"] = rows[-1]["volume"]
    out["prev_close"] = rows[-2]["close"] if len(rows) >= 2 else None
    out["date"] = rows[-1]["date"]
    return out


# ─────────────────────────────── Daum ───────────────────────────────


def fetch_daum(code, timeout=15):
    req = urllib.request.Request(DAUM_QUOTE.format(code=code), headers=DAUM_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def daum_facts(j):
    """검증에 쓰는 필드만 추려낸다. 종가는 regularTradePrice, 등락률은 직접 계산."""
    reg = j.get("regularTradePrice")
    prev = j.get("prevClosingPrice")
    chg = None
    if reg and prev:
        chg = (reg / prev - 1.0) * 100.0
    return {
        "name": j.get("name"),
        "market": j.get("market"),
        "tradeDate": j.get("tradeDate"),
        "close": reg,
        "prev_close": prev,
        "open": j.get("openingPrice"),
        "high": j.get("highPrice"),
        "low": j.get("lowPrice"),
        "volume": j.get("accTradeVolume"),
        "chg_pct": chg,
        "high_52w": j.get("high52wPrice"),
        "low_52w": j.get("low52wPrice"),
        "high_52w_date": j.get("high52wDate"),
        "low_52w_date": j.get("low52wDate"),
        "listing_date": j.get("listingDate"),
        "market_cap": j.get("marketCap"),
    }


# ─────────────────────────────── 검사 ───────────────────────────────


def check_daum(code, ana, dm, errors, warnings):
    """A. Daum 정규장 값 ↔ analysis"""
    pairs = [
        ("close", "종가", 0.5),
        ("prev_close", "전일종가", 0.5),
        ("open", "시가", 0.5),
        ("high", "고가", 0.5),
        ("low", "저가", 0.5),
    ]
    for key, label, tol in pairs:
        a = _num(ana.get(key))
        d = _num(dm.get(key))
        if a is None or d is None:
            warnings.append("%s %s: 비교 불가 (analysis=%s, daum=%s)" % (code, label, a, d))
            continue
        if not _close(a, d, tol):
            errors.append(
                "%s %s 불일치 — analysis %s / Daum %s" % (code, label, a, d)
            )

    a_chg = _num(ana.get("chg_pct"))
    d_chg = dm.get("chg_pct")
    if a_chg is not None and d_chg is not None and abs(a_chg - d_chg) > 0.02:
        errors.append(
            "%s 등락률 불일치 — analysis %.2f%% / Daum 계산 %.4f%% "
            "(regularTradePrice/prevClosingPrice-1)" % (code, a_chg, d_chg)
        )

    a_vol = _num(ana.get("volume"))
    d_vol = _num(dm.get("volume"))
    if a_vol and d_vol and not _pct_close(a_vol, d_vol, 3.0):
        warnings.append(
            "%s 거래량 차이 3%% 초과 — analysis %s / Daum %s "
            "(Daum accTradeVolume 은 시간외 포함이라 장 마감 뒤에는 늘어난다)"
            % (code, a_vol, d_vol)
        )

    # 🔴 사고 24 — build.py 는 240거래일 창, Daum 은 진짜 52주
    for key, label in (("high_52w", "52주 고점"), ("low_52w", "52주 저점")):
        a = _num(ana.get(key))
        d = _num(dm.get(key))
        if a is None or d is None:
            continue
        if not _close(a, d, 1.0):
            pct = None
            c = _num(ana.get("close"))
            if c and d:
                pct = abs(d - c) / d * 100.0
            errors.append(
                "%s %s 불일치 — analysis %s / Daum %s (%s) · 사고 24: build.py 가 "
                "240거래일 창으로 계산함. 본문의 %s 과 「%s 대비 N%%」 를 모두 Daum 값 기준으로 "
                "고칠 것%s"
                % (code, label, a, d,
                   dm.get(key.replace("_52w", "_52w_date")) or "", label, label,
                   (" (올바른 값: %.1f%%)" % pct) if pct is not None else "")
            )


def check_indicators(code, ana, rc, errors, warnings):
    """B. CSV 재계산 ↔ analysis"""
    ma = ana.get("ma") or {}
    for n in (5, 10, 20, 240):
        a = _num(ma.get("ma%d" % n))
        c = rc.get("ma%d" % n)
        if a is None or c is None:
            continue
        if not _close(a, c, max(1.0, abs(c) * 0.0005)):
            errors.append("%s ma%d 불일치 — analysis %s / CSV 재계산 %.1f" % (code, n, a, c))

    for key, tol in (("rsi", 0.15), ("macd", 0.2), ("macd_sig", 0.2), ("macd_hist", 0.2),
                     ("tenkan", 1.0), ("kijun", 1.0)):
        a = _num(ana.get(key))
        c = rc.get(key)
        if a is None or c is None:
            continue
        if not _close(a, c, max(tol, abs(c) * 0.001)):
            errors.append("%s %s 불일치 — analysis %s / CSV 재계산 %.2f" % (code, key, a, c))

    # analysis 에 아예 없는 값 — 정배열 판정에 반드시 필요
    for n in (60, 120):
        if rc.get("ma%d" % n) is not None:
            warnings.append("%s ma%d = %.1f (analysis 에 없음 — 정배열 판정에 사용)" % (code, n, rc["ma%d" % n]))


def arrangement_state(rc):
    """정배열이 어디까지 성립하는지 판정."""
    order = [rc.get("ma%d" % n) for n in (5, 10, 20, 60, 120, 240)]
    names = ["ma5", "ma10", "ma20", "ma60", "ma120", "ma240"]
    have = [(n, v) for n, v in zip(names, order) if v is not None]
    broken = []
    for i in range(len(have) - 1):
        if have[i][1] <= have[i + 1][1]:
            broken.append("%s(%.1f) <= %s(%.1f)" % (have[i][0], have[i][1],
                                                    have[i + 1][0], have[i + 1][1]))
    # 단기(5>10>20)만 보는 판정
    short_ok = True
    for i in range(2):
        if have[i][1] <= have[i + 1][1]:
            short_ok = False
    return {"full": not broken, "short": short_ok, "broken": broken,
            "values": {n: v for n, v in have}}


def split_sections(text, names):
    """본문을 종목별 구간으로 나눈다.

    문단을 돌면서 종목명이 나오면 그 뒤부터 해당 종목 구간으로 본다.
    어느 종목도 안 나온 앞부분은 모든 종목에 공통으로 넣는다.
    """
    parts = re.split(r"(?<=[.!?\n])\s+", text)
    cur = None
    buckets = {n: [] for n in names}
    common = []
    for part in parts:
        hit = [n for n in names if n in part]
        if len(hit) == 1:
            cur = hit[0]
        elif len(hit) > 1:
            cur = None
        if cur:
            buckets[cur].append(part)
        else:
            common.append(part)
    return {n: " ".join(v) for n, v in buckets.items()}, " ".join(common)


SCOPE_WORDS = ("단기", "중기", "5·10·20", "5,10,20", "일부", "부분")


def check_arrangement(code, name, body, rc, errors, warnings):
    """C. 「정배열」 서술 범위"""
    st = arrangement_state(rc)
    if st["full"]:
        return st
    # 전 구간 정배열이 아닌데 본문에 '정배열' 이 있는가
    for m in re.finditer("정배열", body):
        ctx = body[max(0, m.start() - 60): m.start() + 20]
        if any(w in ctx for w in SCOPE_WORDS):
            continue
        if "미정렬" in ctx or "아닙니다" in body[m.start(): m.start() + 40]:
            continue
        errors.append(
            "%s(%s) 「정배열」 범위 과장 — 깨진 구간: %s · 범위를 한정하거나 ma60·ma120 을 명시할 것"
            % (name, code, " / ".join(st["broken"]))
        )
        break
    return st


def check_candle(code, name, body, rc, errors, warnings):
    """D. 캔들 4값 관계 서술 (사고 37)"""
    o, h, l, c = rc.get("open"), rc.get("high"), rc.get("low"), rc.get("close")
    if None in (o, h, l, c):
        return
    upper = h - max(o, c)
    lower = min(o, c) - l
    claims = [
        ("아래꼬리 없", lower == 0),
        ("아랫꼬리 없", lower == 0),
        ("위꼬리 없", upper == 0),
        ("윗꼬리 없", upper == 0),
        ("시가와 고가가 같", o == h),
        ("시가와 저가가 같", o == l),
        ("종가와 고가가 같", c == h),
        ("종가와 저가가 같", c == l),
    ]
    for phrase, truth in claims:
        if phrase in body and not truth:
            errors.append(
                "%s(%s) 캔들 서술 모순 — 「%s」 인데 실제 시 %s / 고 %s / 저 %s / 종 %s "
                "(윗꼬리 %s, 아랫꼬리 %s)" % (name, code, phrase, o, h, l, c, upper, lower)
            )
    if "양봉" in body and c < o:
        errors.append("%s(%s) 「양봉」 인데 종가 %s < 시가 %s" % (name, code, c, o))
    if "음봉" in body and c > o:
        errors.append("%s(%s) 「음봉」 인데 종가 %s > 시가 %s" % (name, code, c, o))


# 값이 실린 숫자만 본다 — 뒤에 원/%/주/배 가 붙은 것.
# ma5·RSI(14)·120일·52주·20거래일 같은 구조적 숫자는 애초에 안 걸린다.
NUM_RE = re.compile(
    r"(?<![\d.])([\d,]*\d(?:\.\d+)?)\s*(원|%|주|배)"
)


def known_values(ana, rc, dm):
    """원고에 나와도 되는 숫자 집합."""
    vals = set()

    def add(v):
        if v is None:
            return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        vals.add(round(f, 4))
        vals.add(round(f, 2))
        vals.add(round(f, 1))
        vals.add(float(int(round(f))))

    for src in (ana, rc, dm):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if isinstance(v, (int, float)):
                add(v)
            elif isinstance(v, dict):
                for vv in v.values():
                    add(vv)
    # 파생값 — 분모가 지표인 비율, 분모가 종가인 비율 양쪽 다 허용 범위에 넣고
    # 어느 쪽인지는 사람이 본다 (E 는 '출처 불명' 만 잡는 게 목적)
    close = rc.get("close")
    if close:
        for k, v in list(rc.items()):
            if isinstance(v, (int, float)) and v and k.startswith(
                    ("ma", "tenkan", "kijun", "recent", "high", "low", "prev")):
                add((close / v - 1.0) * 100.0)   # 지표가 분모
                add((v / close - 1.0) * 100.0)   # 종가가 분모
                add(abs(close - v))
                add(abs(v - close) / v * 100.0)
    o, h, l, c = rc.get("open"), rc.get("high"), rc.get("low"), rc.get("close")
    if None not in (o, h, l, c):
        for a, b in ((h, c), (h, o), (h, l), (c, o), (o, l), (c, l)):
            if b:
                add(abs(a - b))
                add(abs(a - b) / b * 100.0)
            if a:
                add(abs(a - b) / a * 100.0)
    return vals


def check_numbers(code, name, body, vals, errors, warnings):
    """E. 출처를 못 찾은 숫자 뽑기 (해당 종목 구간 안에서만)"""
    unknown = []
    for m in NUM_RE.finditer(body):
        raw, unit = m.group(1), m.group(2)
        f = _num(raw)
        if f is None:
            continue
        if unit == "원" and f < 100:
            continue
        if unit in ("배", "%") and f == 0:
            continue
        cand = {round(f, 4), round(f, 2), round(f, 1), float(int(round(f)))}
        if cand & vals:
            continue
        ctx = re.sub(r"\s+", " ", body[max(0, m.start() - 40): m.start() + 40]).strip()
        unknown.append({"value": raw + unit, "context": ctx})
    if unknown:
        warnings.append(
            "%s(%s) 출처 불명 수치 %d건 — analysis·CSV·Daum 어디에서도 값을 못 찾음: %s"
            % (name, code, len(unknown), ", ".join(u["value"] for u in unknown[:8]))
        )
    return unknown


def check_chart(root, code, date, errors, warnings):
    """F. 차트 PNG"""
    p = os.path.join(root, "out", "charts", "%s_%s.png" % (code, date))
    if not os.path.exists(p):
        errors.append("%s 차트 PNG 없음 — %s" % (code, p))
        return None
    size = os.path.getsize(p)
    if size < 20000:
        errors.append("%s 차트 PNG 가 너무 작음 (%d bytes)" % (code, size))
    return {"path": p, "bytes": size}


# ─────────────────────────────── 메인 ───────────────────────────────


def find_manuscript(root, date):
    d = os.path.join(root, "claude")
    if not os.path.isdir(d):
        return None
    cands = [f for f in os.listdir(d) if f.endswith("%s.md" % date) and f.startswith("발행분-")]
    if not cands:
        return None
    cands.sort()
    return os.path.join(d, cands[-1])


def body_of(path):
    raw = open(path, encoding="utf-8").read()
    marker = "## 본문 HTML"
    i = raw.find(marker)
    return raw, (raw[i + len(marker):] if i >= 0 else raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYYMMDD (한국 증시 거래일)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None)
    ap.add_argument("--daum-cache", default=None,
                    help="오프라인 테스트용: {코드: quotes 응답} JSON")
    ap.add_argument("--no-daum", action="store_true")
    args = ap.parse_args()

    root, date = args.root, args.date
    mpath = find_manuscript(root, date)
    if not mpath:
        print("원고 없음: claude/발행분-*-%s.md" % date, file=sys.stderr)
        return 2

    raw, body = body_of(mpath)
    text = re.sub(r"<[^>]+>", " ", body)

    codes = sorted(set(re.findall(r"out/charts/(\d{6})_%s\.png" % date, body)))
    if not codes:
        codes = sorted(set(re.findall(r"\((\d{6})\)", raw)))

    cache = {}
    if args.daum_cache:
        cache = json.load(open(args.daum_cache, encoding="utf-8"))

    errors, warnings = [], []
    stocks = {}

    # ── 1패스: 종목별 데이터 적재 ──
    loaded = []
    for code in codes:
        apath = os.path.join(root, "out", "analysis", "%s.json" % code)
        cpath = os.path.join(root, "data", "%s.csv" % code)
        if not os.path.exists(apath) or not os.path.exists(cpath):
            errors.append("%s analysis/CSV 없음" % code)
            continue
        ana = json.load(open(apath, encoding="utf-8"))
        rows = load_csv(cpath)
        if not rows:
            errors.append("%s CSV 비어 있음" % code)
            continue
        loaded.append((code, ana, rows, recompute(rows), ana.get("name") or code))

    # 본문을 종목별 구간으로 나눈다 (한 글에 여러 종목이 섞여 있으므로)
    sections, common = split_sections(text, [x[4] for x in loaded])

    daum_all = {}
    if not args.no_daum:
        for code, _a, _r, _rc, _n in loaded:
            try:
                j = cache[code] if code in cache else fetch_daum(code)
                daum_all[code] = daum_facts(j)
            except Exception as e:
                warnings.append("%s Daum 조회 실패 (%s) — 수동 확인 필요" % (code, e))

    # ── 2패스: 검사 ──
    for code, ana, rows, rc, name in loaded:
        seg = (sections.get(name) or "") + " " + common

        a_vol, c_vol = _num(ana.get("volume")), rc.get("volume")
        if a_vol and c_vol and not _close(a_vol, c_vol, 1.0):
            errors.append("%s 거래량 불일치 — analysis %s / CSV %s" % (code, a_vol, c_vol))

        if rc["date"].replace("-", "") != date:
            errors.append("%s CSV 마지막 행이 %s — 기준 거래일 %s 와 다름" % (code, rc["date"], date))

        dm = daum_all.get(code, {})
        if dm:
            if dm.get("tradeDate") and dm["tradeDate"] != date:
                warnings.append("%s Daum tradeDate %s ≠ 기준일 %s" % (code, dm["tradeDate"], date))
            check_daum(code, ana, dm, errors, warnings)

        check_indicators(code, ana, rc, errors, warnings)
        st = check_arrangement(code, name, seg, rc, errors, warnings)
        check_candle(code, name, seg, rc, errors, warnings)
        vals = set()
        for _c, _a, _r, _rc, _n in loaded:
            vals |= known_values(_a, _rc, daum_all.get(_c, {}))
        unknown = check_numbers(code, name, seg, vals, errors, warnings)
        chart = check_chart(root, code, date, errors, warnings)

        # 240일선 언급 시 상장 경과 (사고 19)
        if "240" in text and dm.get("listing_date"):
            try:
                ld = dt.date.fromisoformat(dm["listing_date"])
                if (dt.date.fromisoformat(rc["date"]) - ld).days < 400:
                    errors.append("%s 상장 %s — 240거래일 미경과 가능 (사고 19)" % (code, dm["listing_date"]))
            except Exception:
                pass

        stocks[code] = {
            "name": name,
            "recomputed": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in rc.items()},
            "daum": dm,
            "arrangement": st,
            "unknown_numbers": unknown,
            "chart": chart,
        }

    result = {
        "trading_date": date,
        "manuscript": os.path.relpath(mpath, root),
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
        "status": "fail" if errors else "pass",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "stocks": stocks,
    }

    outdir = args.out or os.path.join(root, "out", "verify")
    os.makedirs(outdir, exist_ok=True)
    opath = os.path.join(outdir, "%s.json" % date)
    with open(opath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print("=" * 60)
    print("verify %s → %s" % (date, result["status"].upper()))
    print("원고: %s" % result["manuscript"])
    print("종목: %s" % ", ".join("%s(%s)" % (v["name"], k) for k, v in stocks.items()))
    print("-" * 60)
    for e in errors:
        print("[ERROR] %s" % e)
    for w in warnings:
        print("[warn ] %s" % w)
    for code, s in stocks.items():
        for u in s["unknown_numbers"]:
            print("[num  ] %s 출처불명 %s … %s" % (code, u["value"], u["context"]))
    print("-" * 60)
    print("결과 파일: %s" % opath)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
