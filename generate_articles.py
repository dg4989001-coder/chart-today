#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek API로 블로그 원고 5편 자동 생성
- 입력: out/market.json, out/analysis/<code>.json, picked[].news
- 출력: claude/발행분-XXX-YYYYMMDD.md
"""
import json, os, re, sys
from datetime import datetime
from pathlib import Path
import requests

ROOT = Path(__file__).parent
OUT = ROOT / "out"
CLAUDE_DIR = ROOT / "claude"
CLAUDE_DIR.mkdir(exist_ok=True)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    sys.exit("DEEPSEEK_API_KEY 환경변수가 없습니다")

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

LARGE_CAP_NAMES = {
    "KB금융", "삼성전자", "SK하이닉스", "현대차", "네이버", "카카오",
    "LG에너지솔루션", "삼성바이오로직스", "기아", "셀트리온", "포스코홀딩스",
    "현대모비스", "LG화학", "삼성SDI", "카카오뱅크", "한화오션", "HD현대중공업",
    "신한지주", "하나금융지주", "우리금융지주", "삼성물산", "LG전자", "SK",
    "현대글로비스", "크래프톤", "엔씨소프트", "넷마블", "SK텔레콤", "KT",
}

SYSTEM_PROMPT = """당신은 한국 주식 블로그 「차트로 보는 오늘」의 원고를 작성하는 전문 작가입니다.

## 블로그 철칙 (최우선)
"모든 걸 사실대로, 결과물은 오류 없이 정확하게. 확인 안 된 숫자는 쓰지 않고 뺍니다."
- 존댓말, 1,500~2,500자
- 어그로 질문형 제목
- 매수·매도 권유 금지, 목표주가 단정 금지, 면책 문구 필수
- 매체마다 수치 다르면 그 숫자는 버린다
- 주어진 데이터에 없는 숫자는 절대 지어내지 않는다

## 콘텐츠 철학
"우리는 상한가 뒤쫓는 사람이 아니다. 현재 차트의 흐름을 읽어서 앞으로 어떻게 대응할지 조언하는 사람이다."

## 9단계 템플릿 (반드시 이 구조)
1. 도입 — 오늘 종가, 등락률, 거래량 배수 (굵은 글씨 GEO 요약 1문장)
2. 최근 뉴스 — 요약 + 출처 링크 (없으면 생략)
3. 차트 이미지 (<a target="_blank">로 감싸기)
4. "오늘 장중 X%와 마감 Y%의 차이" 소제목
5. "다음 언덕은 어디인가" — 저항/지지 레벨
6. 이동평균선·보조지표 표 (지표/값/해석 3열, <thead>/<th> 금지, <tbody> 첫 행 <td><b>)
7. 무효화 조건 3단계 (1차/2차/3차)
8. 포지션 사이징 (자본 1,000만 원, 1% 룰, 버림 계산)
9. 시나리오 A/B
10. "오늘 이 종목에서 배울 것"
11. 면책 문구 5줄 + 누적 수익률 링크

## GEO 원칙
- 도입부 최상단에 굵게 처리한 1~2문장 사실 요약 (날짜+종목명+코드+가격+등락률+핵심 지표)
- 소제목은 자연어 질문형
- 구체적 숫자·출처 링크 유지

## 금지 문체 (절대 쓰지 말 것)
- "이 글에서는 ~을 알아보겠습니다"
- "지금부터 ~을 살펴보겠습니다"
- "본 글은 ~에 대해 다룹니다"
- "함께 살펴볼까요?"
- 메타 설명 문장 전부

## HTML 출력 규칙
- 마크다운 코드블록 없이 순수 HTML만
- 이미지: <a href="URL" target="_blank"><img src="URL" alt="..." style="max-width:100%;height:auto;"></a>
- 표: <table style="border-collapse:collapse;width:100%;" border="1"> + <tbody> 첫 행 <td><b>헤더</b></td>
- 면책문구 5줄:
  ※ 본 글은 기업 및 주가에 대한 정보와 분석을 제공하기 위한 목적으로 작성되었으며, 특정 금융투자상품의 매수·매도 또는 투자를 권유하지 않습니다.
  ※ 본문에 포함된 실적 전망, 목표주가, 예상 EPS 등 미래 관련 수치는 시장 컨센서스 또는 전망치이며 확정된 결과가 아닙니다.
  ※ 차트의 주요 가격대와 기술적 분석은 작성 시점의 데이터를 기준으로 한 분석 의견이며, 향후 주가 움직임을 보장하지 않습니다.
  ※ 투자 판단은 각 투자자의 독립적인 판단과 책임에 따라 이루어져야 합니다.
  ※ 작성자는 해당 종목을 보유하지 않고 있으며, 본 글은 이해관계와 무관하게 작성되었습니다.
- 마지막: ▶ <a href="https://mynote86824.tistory.com/pages/누적-수익률">누적 수익률 전체 보기</a>
"""


def fetch_daum_quote(code: str) -> dict | None:
    """Daum API로 종가·등락률 재검증. 실패 시 None."""
    url = f"https://finance.daum.net/api/quotes/A{code}?summary=false"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://finance.daum.net/quotes/A{code}",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        j = r.json()["data"]
        return {
            "close": int(j["tradePrice"]),
            "prev_close": int(j["prevClosingPrice"]),
            "chg_pct": round(float(j["changeRate"]) * 100, 2),
            "high": int(j["highPrice"]),
            "low": int(j["lowPrice"]),
            "volume": int(j["accTradeVolume"]),
        }
    except Exception as e:
        print(f"[warn] Daum {code}: {e}", file=sys.stderr)
        return None


def call_deepseek(user_prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 8000,
    }
    r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def build_user_prompt(pick, analysis, news_list, chart_url):
    is_large = pick["name"] in LARGE_CAP_NAMES or pick.get("mcap", 0) >= 10_000_000_000_000

    news_text = "관련 뉴스 없음"
    if news_list:
        lines = [f"- [{n['office']}] {n['title']} ({n['url']})" for n in news_list[:3]]
        news_text = "\n".join(lines)

    ma = analysis["ma"]
    return f"""다음 종목의 차트 데이터를 바탕으로 블로그 원고 1편을 작성해주세요.

[종목 정보]
- 종목명: {pick['name']}
- 종목코드: {pick['code']}
- 시장: {pick['market']}
- 종목 소개: {"생략 (대기업이므로 '이 회사는 무엇을 하는가' 섹션 제외)" if is_large else "포함 필요"}

[차트 데이터 - {analysis['asof']} 기준]
- 종가: {analysis['close']:,}원
- 전일 종가: {analysis['prev_close']:,}원
- 등락률: {analysis['chg_pct']:+.2f}%
- 고가: {analysis['high']:,}원
- 저가: {analysis['low']:,}원
- 거래량: {analysis['volume']:,}주
- 거래량 배수 (직전 20거래일 평균 대비): {analysis['vol_ratio']}배
- 이평선: ma5={ma['ma5']:,} / ma10={ma['ma10']:,} / ma20={ma['ma20']:,} / ma240={ma['ma240']:,}
- 이평선 배열: {analysis['arrangement']}
- 240일선 대비: {analysis['vs_ma240_pct']:+.1f}%
- 주봉 20선: {analysis['weekly_ma20']:,}
- MACD: {analysis['macd']} / 시그널: {analysis['macd_sig']} / 히스토그램: {analysis['macd_hist']}
- MACD 크로스: {analysis['macd_cross']} ({analysis['macd_cross_days_ago']}거래일 전)
- RSI(14): {analysis['rsi']}
- 120일 고점: {analysis['recent120_high']:,}원
- 120일 저점: {analysis['recent120_low']:,}원
- 52주 고점: {analysis['high_52w']:,}원
- 52주 저점: {analysis['low_52w']:,}원
- 52주 고점 대비: {analysis['drawdown_from_52w_high_pct']:+.1f}%
- 일목 전환선: {analysis['tenkan']:,}원 / 기준선: {analysis['kijun']:,}원

[뉴스]
{news_text}

[차트 이미지 URL]
{chart_url}

[출력 요구사항]
반드시 JSON 형식으로만 응답:
{{
  "title": "제목 (어그로 질문형, 40자 이내)",
  "tags": "#태그1 #태그2 #태그3 #태그4",
  "body_html": "본문 HTML (마크다운 코드블록 없이 순수 HTML만)"
}}
"""


def get_next_issue_number() -> int:
    files = list(CLAUDE_DIR.glob("발행분-*.md"))
    nums = []
    for f in files:
        m = re.search(r"발행분-(\d+)", f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 9


def main():
    market = json.load(open(OUT / "market.json", encoding="utf-8"))
    day = market["date"]
    ymd = day.replace("-", "")

    picked = market["picked"]
    if not picked:
        sys.exit("picked 종목이 없습니다")

    issue_num = get_next_issue_number()
    print(f"[시작] 발행분 {issue_num:03d} — {day}, {len(picked)}종목")

    articles = []
    for p in picked:
        code = p["code"]
        apath = OUT / "analysis" / f"{code}.json"
        if not apath.exists():
            print(f"[skip] {code} {p['name']}: analysis 없음")
            continue

        analysis = json.load(open(apath, encoding="utf-8"))
        

        # Daum 재검증 — 원고 생성 전 종가·등락률 덮어쓰기
        dq = fetch_daum_quote(code)
        if dq:
            analysis["close"] = dq["close"]
            analysis["prev_close"] = dq["prev_close"]
            analysis["chg_pct"] = dq["chg_pct"]
            analysis["high"] = dq["high"]
            analysis["low"] = dq["low"]
            analysis["volume"] = dq["volume"]
            print(f"[Daum] {p['name']} 검증: {dq['chg_pct']:+.2f}%")
        else:
            print(f"[warn] {p['name']} Daum 검증 실패 → analysis 값 사용", file=sys.stderr)
      
        news_list = p.get("news", [])
        chart_url = (
            f"https://raw.githubusercontent.com/dg4989001-coder/chart-today"
            f"/main/out/charts/{code}_{ymd}.png"
        )

        print(f"[생성] {p['name']} ({code})...")
        try:
            content = call_deepseek(build_user_prompt(p, analysis, news_list, chart_url))
            # 마크다운 코드블록 제거
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            data = json.loads(content)
            articles.append({
                "code": code,
                "name": p["name"],
                "title": data["title"],
                "tags": data["tags"],
                "body_html": data["body_html"],
            })
            print(f"[완료] {p['name']} — {data['title']}")
        except Exception as e:
            print(f"[실패] {p['name']}: {e}", file=sys.stderr)

    if not articles:
        sys.exit("생성된 원고가 없습니다")

    md = [
        f"# 발행 지시서 — {day} ({len(articles)}편)",
        "작성: DeepSeek API · 발행: 클로드",
        "",
        "## 공통 정보",
        "- 블로그: https://mynote86824.tistory.com",
        "- 카테고리: 오늘의 핫종목",
        "- 발행 방식: **비공개** (오빠 확인 후 공개 전환)",
        "- 발행 전 검증: Daum API로 종가·등락률 대조, 금지 문체 확인",
        "",
        "---",
        "",
    ]
    for i, a in enumerate(articles, 1):
        md += [
            f"## {i}편 — {a['name']} ({a['code']})",
            "",
            "### 제목",
            a["title"],
            "",
            "### 태그",
            a["tags"],
            "",
            "### 본문 HTML",
            a["body_html"],
            "",
            "---",
            "",
        ]

    out_path = CLAUDE_DIR / f"발행분-{issue_num:03d}-{ymd}.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n[저장] {out_path} ({len(articles)}편)")


if __name__ == "__main__":
    main()
