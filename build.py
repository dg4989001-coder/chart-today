#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
차트로 보는 오늘 — 종목 차트/지표 생성기
데이터: Yahoo Finance 일봉 (KRX)
출력  : charts/<code>.png  +  analysis/<code>.json
"""
import json, math, os, shutil, sys
import pandas as pd, numpy as np
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import glob

for p in glob.glob('/usr/share/fonts/truetype/nanum/*.ttf'):
    fm.fontManager.addfont(p)
plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(os.path.abspath(__file__))
CHARTS, ANALYSIS = f"{BASE}/out/charts", f"{BASE}/out/analysis"
os.makedirs(CHARTS, exist_ok=True)
os.makedirs(ANALYSIS, exist_ok=True)

# ── 색 ───────────────────────────────────────────────────────────
UP, DOWN = "#D32F2F", "#1565C0"          # 한국 관행: 상승 적, 하락 청
MA_RAMP = {5: "#9B7EDE", 20: "#7A4FD0", 60: "#5B2CA8", 240: "#35176B"}  # 순차 램프(기간=크기)
INK, INK2, INK3 = "#1a1a1a", "#5b5b5b", "#8a8a8a"
GRID, SURF = "#e8e8e8", "#ffffff"
ACC = "#B45309"                           # 주석 강조

# ── 지표 ──────────────────────────────────────────────────────────
def add_indicators(df):
    c = df["close"]
    for n in (5, 10, 20, 60, 120, 240):
        df[f"ma{n}"] = c.rolling(n).mean()
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    df["macd"] = e12 - e26
    df["macd_sig"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_sig"]
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    # "평소의 몇 배냐"는 당일을 뺀 직전 20거래일 평균으로 잰다.
    # 당일을 평균에 넣으면 급등일의 거래량이 분모를 스스로 끌어올려 배수가 희석된다.
    df["vol_ma20_prev"] = df["volume"].rolling(20).mean().shift(1)
    return df

def weekly_ma20(df):
    """주봉 20선 — 주간 마지막 거래일 종가 기준"""
    w = df.set_index("date")["close"].resample("W-FRI").last().dropna()
    return float(w.rolling(20).mean().iloc[-1]) if len(w) >= 20 else float("nan")

def last_cross(fast, slow):
    """가장 최근 교차: (방향, 며칠 전). 방향 golden/dead"""
    diff = (fast - slow).dropna()
    s = np.sign(diff)
    for i in range(len(s) - 1, 0, -1):
        if s.iloc[i] != s.iloc[i-1] and s.iloc[i] != 0:
            return ("golden" if s.iloc[i] > 0 else "dead"), len(s) - 1 - i
    return None, None

def arrangement(r):
    """이동평균선 배열 판정"""
    v = [r["close"], r["ma5"], r["ma20"], r["ma60"], r["ma120"], r["ma240"]]
    if any(pd.isna(x) for x in v):
        return "판정불가"
    if all(v[i] > v[i+1] for i in range(len(v)-1)):
        return "정배열"
    if all(v[i] < v[i+1] for i in range(len(v)-1)):
        return "역배열"
    short_ok = r["close"] > r["ma5"] > r["ma20"]
    return "단기 정배열(중장기 미정렬)" if short_ok else "혼조"

# ── 차트 ──────────────────────────────────────────────────────────
def won(v, _=None):
    v = float(v)
    if v >= 1000000: return f"{v/10000:,.0f}만"
    return f"{v:,.0f}"

# ── 빈 자리 찾기 ──────────────────────────────────────────────────
NC, NR = 30, 16   # 가격 패널을 나눈 격자

def occupancy(d, ylim):
    """캔들·이동평균선이 차지한 격자 = True"""
    lo, hi = ylim; span = hi - lo
    occ = np.zeros((NR, NC), dtype=bool)
    n = len(d)
    def mark(col, y0, y1):
        if col < 0 or col >= NC: return
        r0 = int(np.clip((y0 - lo) / span * NR, 0, NR - 1))
        r1 = int(np.clip((y1 - lo) / span * NR, 0, NR - 1))
        occ[min(r0, r1):max(r0, r1) + 1, col] = True
    for i, r in d.iterrows():
        mark(int(i / n * NC), r["low"], r["high"])
    for k in (5, 20, 60, 240):
        s = d[f"ma{k}"]
        for i, v in s.items():
            if pd.notna(v): mark(int(i / n * NC), v, v)
    return occ

def find_slot(occ, w, h, prefer, taken, ignore_occ=False):
    """폭 w·높이 h(격자 단위) 빈 자리를 prefer(축 비율)에 가장 가깝게.
    ignore_occ=True면 차트선 위는 허용하되 다른 말풍선과는 절대 겹치지 않음."""
    pc, pr = prefer[0] * NC, prefer[1] * NR
    best, bestd = None, 1e9
    w = min(w, NC); h = min(h, NR)
    for r in range(NR - h + 1):
        for c in range(NC - w + 1):
            if taken[r:r+h, c:c+w].any(): continue
            if not ignore_occ and occ[r:r+h, c:c+w].any(): continue
            dist = ((c + w/2 - pc) / NC) ** 2 + ((r + h/2 - pr) / NR) ** 2
            if dist < bestd: bestd, best = dist, (r, c)
    if best is None: return None
    r, c = best
    taken[max(0, r-1):r+h+1, max(0, c-1):c+w+1] = True
    return ((c + w/2) / NC, (r + h/2) / NR)

def draw(df, code, name, asof, window=120, notes=None, info=None):
    d = df.tail(window).reset_index(drop=True)
    x = np.arange(len(d))
    fig = plt.figure(figsize=(11.5, 9.6), dpi=130, facecolor=SURF)
    gs = fig.add_gridspec(4, 1, height_ratios=[5.0, 1.15, 1.25, 1.15], hspace=0.10,
                          left=0.075, right=0.865, top=0.905, bottom=0.062)
    axp, axv, axm, axr = [fig.add_subplot(gs[i]) for i in range(4)]

    for ax in (axp, axv, axm, axr):
        ax.set_facecolor(SURF)
        for s in ("top", "right"): ax.spines[s].set_visible(False)
        for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
        ax.tick_params(colors=INK3, labelsize=8.5, length=0)
        ax.grid(True, color=GRID, lw=0.6, alpha=0.9)
        ax.set_axisbelow(True)
        ax.margins(x=0.01)

    # 캔들
    w = 0.62
    for i, r in d.iterrows():
        col = UP if r["close"] >= r["open"] else DOWN
        axp.vlines(i, r["low"], r["high"], color=col, lw=0.9, zorder=3)
        lo, hi = min(r["open"], r["close"]), max(r["open"], r["close"])
        axp.add_patch(plt.Rectangle((i - w/2, lo), w, max(hi - lo, (r["high"]-r["low"])*0.004 or 1),
                                    facecolor=col, edgecolor=col, lw=0.4, zorder=4))
    # 이동평균선
    labels = []
    for n, col in MA_RAMP.items():
        if d[f"ma{n}"].notna().sum() == 0: continue
        axp.plot(x, d[f"ma{n}"], color=col, lw=1.7, zorder=5, solid_capstyle="round")
        yv = d[f"ma{n}"].iloc[-1]
        if pd.notna(yv): labels.append([float(yv), f"{n}일선", col])
    axp.yaxis.set_major_formatter(FuncFormatter(won))
    axp.set_xticklabels([])

    # 우측 직접 라벨 — 겹치면 세로로 밀어냄
    lo_, hi_ = axp.get_ylim(); gap = (hi_ - lo_) * 0.035
    labels.sort(key=lambda t: t[0])
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i-1][0] < gap:
            labels[i][0] = labels[i-1][0] + gap
    for yv, txt, col in labels:
        axp.annotate(txt, xy=(x[-1], yv), xytext=(8, 0), textcoords="offset points",
                     color=col, fontsize=9.5, va="center", fontweight="bold", annotation_clip=False)

    last = d.iloc[-1]
    chg = (last["close"] / d.iloc[-2]["close"] - 1) * 100
    sign = "▲" if chg > 0 else ("▼" if chg < 0 else "―")
    fig.text(0.075, 0.962, f"{name} ({code})", fontsize=17, fontweight="bold", color=INK)
    fig.text(0.075, 0.930, f"{asof} 종가 {last['close']:,.0f}원  {sign}{abs(chg):.2f}%"
                           f"   ·   거래량 {last['volume']/10000:,.0f}만주", fontsize=10.5, color=INK2)
    fig.text(0.865, 0.962, "차트로 보는 오늘", fontsize=10, color=INK3, ha="right")

    # 위아래 여백을 만들어 주석 자리를 확보
    y0, y1 = axp.get_ylim(); pad = (y1 - y0) * 0.10
    axp.set_ylim(y0 - pad, y1 + pad)

    # 빈 자리 계산 후 주석/요약 배치
    occ = occupancy(d, axp.get_ylim())
    taken = np.zeros_like(occ)

    if info:
        slot = find_slot(occ, 9, 5, (0.16, 0.86), taken) or (0.16, 0.86)
        axp.text(slot[0], slot[1], info, transform=axp.transAxes, fontsize=9.5, color=INK,
                 va="center", ha="center", linespacing=1.6, zorder=9,
                 bbox=dict(boxstyle="round,pad=0.5", fc="#FBFAFF", ec="#7A4FD0", lw=1.0, alpha=0.98))

    for nt in (notes or []):
        bw = max(4, int(len(max(nt["text"].split("\n"), key=len)) * 0.55))
        h = 2 + nt["text"].count("\n")
        slot = (find_slot(occ, bw, h, nt["prefer"], taken)
                or find_slot(occ, max(3, bw - 3), h, nt["prefer"], taken)
                or find_slot(occ, bw, h, nt["prefer"], taken, ignore_occ=True)
                or nt["prefer"])
        axp.annotate(nt["text"], xy=(nt["i"], nt["y"]), xycoords="data",
                     xytext=slot, textcoords="axes fraction",
                     fontsize=9.5, color=ACC, fontweight="bold", ha="center", va="center", zorder=9,
                     arrowprops=dict(arrowstyle="->", color=ACC, lw=1.3, shrinkB=5,
                                     connectionstyle="arc3,rad=0.14"),
                     bbox=dict(boxstyle="round,pad=0.35", fc="#FFF7ED", ec=ACC, lw=0.9, alpha=0.98))

    # 거래량
    axv.bar(x, d["volume"], width=w,
            color=[UP if r["close"] >= r["open"] else DOWN for _, r in d.iterrows()], alpha=0.55)
    axv.plot(x, d["vol_ma20"], color=INK3, lw=1.2)
    axv.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/10000:,.0f}만"))
    axv.set_xticklabels([]); axv.set_ylabel("거래량", fontsize=9, color=INK2)

    # MACD
    axm.bar(x, d["macd_hist"], width=w,
            color=[UP if v >= 0 else DOWN for v in d["macd_hist"]], alpha=0.45)
    axm.plot(x, d["macd"], color="#35176B", lw=1.6, label="MACD")
    axm.plot(x, d["macd_sig"], color="#B45309", lw=1.4, label="시그널")
    axm.axhline(0, color=INK3, lw=0.8)
    axm.legend(loc="upper left", fontsize=8, frameon=False, ncol=2, labelcolor=INK2)
    axm.set_xticklabels([]); axm.set_ylabel("MACD", fontsize=9, color=INK2)
    axm.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: "0" if abs(v) < 1e-9 else (f"{v/10000:,.0f}만" if abs(v) >= 10000 else f"{v:,.0f}")))

    # RSI
    axr.plot(x, d["rsi"], color="#5B2CA8", lw=1.6)
    axr.axhline(70, color=UP, lw=0.9, ls=(0, (4, 3)))
    axr.axhline(30, color=DOWN, lw=0.9, ls=(0, (4, 3)))
    axr.axhspan(30, 70, color="#f6f4fb", zorder=0)
    axr.set_ylim(0, 100); axr.set_yticks([30, 50, 70])
    axr.set_ylabel("RSI(14)", fontsize=9, color=INK2)
    axr.annotate(f"{last['rsi']:.0f}", xy=(x[-1], last["rsi"]), xytext=(6, 0),
                 textcoords="offset points", color="#5B2CA8", fontsize=9.5,
                 fontweight="bold", va="center", annotation_clip=False)

    step = max(len(d)//8, 1)
    ticks = list(range(0, len(d), step))
    axr.set_xticks(ticks)
    axr.set_xticklabels([d["date"].iloc[i].strftime("%y.%m.%d") for i in ticks], fontsize=8.5)
    for ax in (axp, axv, axm): ax.set_xticks(ticks)

    fig.text(0.075, 0.018, "데이터: Yahoo Finance 일봉 · 지표는 종가 기준 자체 계산 · "
                           "본 차트는 정보 제공용이며 매매 권유가 아닙니다.",
             fontsize=8, color=INK3)
    out = f"{CHARTS}/{code}.png"
    fig.savefig(out, facecolor=SURF); plt.close(fig)
    return out

# ── 요약 ──────────────────────────────────────────────────────────
def summarize(df, code, name, asof):
    r, p = df.iloc[-1], df.iloc[-2]
    win60, win120 = df.tail(60), df.tail(120)
    hi52, lo52 = df.tail(240)["high"].max(), df.tail(240)["low"].min()
    mk, mdays = last_cross(df["macd"], df["macd_sig"])
    wma20 = weekly_ma20(df)
    s = {
        "code": code, "name": name, "asof": asof,
        "close": int(r["close"]), "prev_close": int(p["close"]),
        "chg_pct": round((r["close"]/p["close"]-1)*100, 2),
        "high": int(r["high"]), "low": int(r["low"]),
        "volume": int(r["volume"]), "vol_ma20": int(r["vol_ma20"]),
        "vol_ma20_prev": None if pd.isna(r["vol_ma20_prev"]) else int(r["vol_ma20_prev"]),
        "vol_ratio": None if pd.isna(r["vol_ma20_prev"]) or r["vol_ma20_prev"] == 0
                     else round(r["volume"]/r["vol_ma20_prev"], 1),
        "ma": {f"ma{n}": (None if pd.isna(r[f"ma{n}"]) else int(round(r[f"ma{n}"])))
               for n in (5, 10, 20, 60, 120, 240)},
        "arrangement": arrangement(r),
        "vs_ma240_pct": None if pd.isna(r["ma240"]) else round((r["close"]/r["ma240"]-1)*100, 1),
        "weekly_ma20": None if math.isnan(wma20) else int(round(wma20)),
        "above_weekly_ma20": None if math.isnan(wma20) else bool(r["close"] > wma20),
        "macd": round(float(r["macd"]), 1), "macd_sig": round(float(r["macd_sig"]), 1),
        "macd_hist": round(float(r["macd_hist"]), 1),
        "macd_cross": mk, "macd_cross_days_ago": mdays,
        "rsi": round(float(r["rsi"]), 1),
        "high_52w": int(hi52), "low_52w": int(lo52),
        "drawdown_from_52w_high_pct": round((r["close"]/hi52-1)*100, 1),
        "recent60_high": int(win60["high"].max()), "recent60_low": int(win60["low"].min()),
        "recent120_high": int(win120["high"].max()), "recent120_low": int(win120["low"].min()),
    }
    return s

def build(code, name, asof_cut=None):
    df = pd.read_csv(f"{BASE}/data/{code}.csv", parse_dates=["date"]).sort_values("date")
    if asof_cut: df = df[df["date"] <= pd.Timestamp(asof_cut)]
    df = add_indicators(df).reset_index(drop=True)
    asof = df["date"].iloc[-1].strftime("%Y.%m.%d")
    s = summarize(df, code, name, asof)

    # 데이터에서 뽑은 주석 (하드코딩 금지)
    d = df.tail(120).reset_index(drop=True)
    ih, il = int(d["high"].idxmax()), int(d["low"].idxmin())
    notes = [
        dict(i=len(d)-1, y=float(d["close"].iloc[-1]), prefer=(0.72, 0.20),
             text=f"{asof}  {s['close']:,}원 ({s['chg_pct']:+.2f}%)"
                  + (f"\n거래량 직전 20일평균의 {s['vol_ratio']}배" if s['vol_ratio'] else "")),
        dict(i=ih, y=float(d["high"].iloc[ih]), prefer=(min(0.85, max(0.15, ih/len(d))), 0.93),
             text=f"120일 고점 {int(d['high'].iloc[ih]):,}원"),
        dict(i=il, y=float(d["low"].iloc[il]), prefer=(min(0.85, max(0.15, il/len(d))), 0.07),
             text=f"120일 저점 {int(d['low'].iloc[il]):,}원"),
    ]
    if s["ma"]["ma240"]:
        j = int(len(d) * 0.45)
        notes.append(dict(i=j, y=float(d["ma240"].iloc[j]), prefer=(0.40, 0.12),
                          text=f"240일선 {s['ma']['ma240']:,}원 · 종가는 {s['vs_ma240_pct']:+.1f}%"))

    wk = s["weekly_ma20"]
    info = (f"이동평균선 배열 : {s['arrangement']}\n"
            f"240일선 대비 : {s['vs_ma240_pct']:+.1f}%\n"
            f"주봉 20선 {wk:,}원 : {'위' if s['above_weekly_ma20'] else '아래'}\n"
            f"RSI(14) : {s['rsi']:.0f}\n"
            f"MACD : {'골든크로스' if s['macd_cross']=='golden' else '데드크로스'} "
            f"{s['macd_cross_days_ago']}거래일 전")
    png = draw(df, code, name, asof, notes=notes, info=info)
    # 날짜를 박은 사본도 남긴다 — 블로그 본문에 링크한 이미지가 나중에 덮어써지지 않도록
    dated = f"{CHARTS}/{code}_{asof.replace('.', '')}.png"
    shutil.copyfile(png, dated)
    with open(f"{ANALYSIS}/{code}.json", "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    return s, png

if __name__ == "__main__":
    mkt_path = f"{BASE}/out/market.json"
    mkt = json.load(open(mkt_path)) if os.path.exists(mkt_path) else {}
    cut = mkt.get("date")

    todo, seen = [], set()
    for p in mkt.get("picked", []):
        if p["code"] not in seen:
            todo.append((p["code"], p["name"], True)); seen.add(p["code"])
    wf = f"{BASE}/watchlist.json"
    watch = json.load(open(wf)) if os.path.exists(wf) else []
    for w in watch:
        if w["code"] not in seen:
            todo.append((w["code"], w["name"], False)); seen.add(w["code"])
    if not todo:   # 로컬 테스트용
        todo = [(os.path.basename(f)[:6], os.path.basename(f)[:6], True)
                for f in sorted(glob.glob(f"{BASE}/data/*.csv"))]

    results, failed = {}, []
    for code, name, is_pick in todo:
        try:
            s_, png = build(code, name, cut)
            results[code] = s_
            print(f"[ok] {name} {code} {s_['asof']} → {png}")
        except Exception as e:
            failed.append({"code": code, "name": name, "error": str(e)})
            print(f"[fail] {name} {code}: {e}", file=sys.stderr)

    # 누적 수익률
    perf = []
    for w in watch:
        r = results.get(w["code"])
        if not r: continue
        perf.append({**w, "last_date": r["asof"], "last_close": r["close"],
                     "return_pct": round((r["close"] / w["intro_close"] - 1) * 100, 2)})
    if perf:
        up   = sum(1 for p in perf if p["return_pct"] > 0)
        down = sum(1 for p in perf if p["return_pct"] < 0)
        summary = {"count": len(perf), "up": up, "down": down,
                   "flat": len(perf) - up - down,
                   "avg_return_pct": round(sum(p["return_pct"] for p in perf) / len(perf), 2)}
    else:
        summary = {"count": 0}
    json.dump({"as_of": cut, "rows": perf, "summary": summary, "failed": failed},
              open(f"{BASE}/out/performance.json", "w"), ensure_ascii=False, indent=1)
    print("\n[누적 수익률]", json.dumps(summary, ensure_ascii=False))
    for p in perf:
        print(f"  {p['name']:<12} {p['intro_close']:>10,} → {p['last_close']:>10,}  {p['return_pct']:+.2f}%")
