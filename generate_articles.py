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
- 존댓말(합니다체)로 처음부터 끝까지 통일한다. 평서체("~했다", "~이다")와 절대 섞지 않는다.
- 1,500~2,500자
- 어그로 질문형 제목
- 매수·매도 권유 금지, 목표주가 단정 금지, 면책 문구 필수
- 구체적인 매수 수량·투입 금액·분할 매수 계획을 제시하지 않는다(예: "9주가 한도", "122만 원어치", "1차 50주"). 리스크 관리는 가격대(손절 기준선)로만 설명한다.
- 매체마다 수치 다르면 그 숫자는 버린다
- 주어진 데이터에 없는 숫자는 절대 지어내지 않는다
- 상장 후 240거래일이 지나지 않은 종목은 240일선을 아예 언급하지 않는다

## 콘텐츠 철학
"우리는 상한가 뒤쫓는 사람이 아니다. 현재 차트의 흐름을 읽어서 앞으로 어떻게 대응할지 조언하는 사람이다."

## 글 구성 — 고정 틀이 아니다 (가장 중요)
아래는 쓸 수 있는 재료 목록일 뿐입니다. **글마다 구성과 순서를 다르게** 가져가야 합니다.
모든 글이 같은 순서, 같은 소제목으로 나오면 실패한 원고입니다.

[필수 — 모든 글에 포함]
- 도입부 맨 위에 굵은 글씨 사실 요약 1~2문장 (날짜 + 종목명 + 코드 + 종가 + 등락률 + 그날의 핵심 지표)
- 차트 이미지 (<a target="_blank">로 감싸기)
- 숫자 근거가 있는 분석 본문
- 면책 문구 5줄 + 누적 수익률 링크

[선택 — 그날 그 종목에 실제로 해당하는 것만 고른다. 전부 넣지 말 것]
- 최근 뉴스 요약 + 출처 링크 (뉴스가 없으면 이 섹션 자체를 뺀다)
- 이동평균선·보조지표 표 (지표/값/해석 3열, <thead>/<th> 금지, <tbody> 첫 행 <td><b>)
- 지지·저항 가격대
- 추세가 무효화되는 가격 조건
- 앞으로 가능한 시나리오
- 이 종목이 보여주는 교훈
- 회사가 무엇을 하는 곳인지 (중소형주일 때만)

[변주 규칙 — 반드시 지킬 것]
- 그날 가장 특이한 사실로 글을 시작한다. 거래량이 터진 날은 거래량에서, 윗꼬리가 길면 그 캔들에서,
  뉴스가 결정적이면 뉴스에서 출발한다. 매번 같은 방식으로 시작하지 않는다.
- 소제목 문구를 재사용하지 않는다. "다음 언덕은 어디인가" 같은 문구를 모든 글에 반복하면 안 된다.
  그 종목, 그날에 맞는 소제목을 새로 쓴다.
- 섹션 개수도 글마다 다르게 한다. 어떤 날은 짧고 밀도 높게, 어떤 날은 길게.
- 표는 꼭 필요한 글에만 쓴다.

## GEO 원칙
- 도입부 최상단에 굵게 처리한 1~2문장 사실 요약 (날짜+종목명+코드+가격+등락률+핵심 지표)
- 소제목은 자연어 질문형 (단, 글마다 다른 문구로 — 같은 소제목을 재사용하지 않는다)
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


def fetch_daum_quote(code: str):
    """Daum API로 종가·등락률 재검증. 실패 시 None.

    2026-09-21 수정 (사고 8/9 대응):
    - r.json()["data"] 는 잘못됨 — 실제 응답은 data 래퍼 없이 최상위에 필드가 옴.
      이 버그 때문에 매번 KeyError -> except 로 빠져서 항상 None을 반환했고,
      결과적으로 "Daum 재검증"이 추가된 뒤에도 실제로는 한 번도 실행되지 않았음.
    - tradePrice 는 시간외(애프터마켓) 체결가라 장중에도 계속 바뀐다(사고 6 참고).
      "오늘 종가"는 반드시 regularTradePrice(정규장 15:30 확정 종가)를 써야 함.
    - changeRate 필드도 tradePrice 기준으로 계산되어 있어 그대로 쓰면 안 되고,
      regularTradePrice / prevClosingPrice 로 직접 재계산한다.
    """
    url = f"https://finance.daum.net/api/quotes/A{code}?summary=false"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://finance.daum.net/quotes/A{code}",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        j = r.json()
        close = int(j["regularTradePrice"])
        prev_close = int(j["prevClosingPrice"])
        chg_pct = round((close / prev_close - 1) * 100, 2)
        return {
            "close": close,
            "prev_close": prev_close,
            "chg_pct": chg_pct,
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
