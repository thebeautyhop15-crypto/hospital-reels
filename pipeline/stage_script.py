"""2단계 대본 — 기획(analysis.json + brief.yml)으로 후킹 문구와 나레이션을 씀 → 02_script/<주차>/script.md

이미 있는 script.md 는 절대 덮어쓰지 않습니다(사람이 고친 대본 보호). 다시 쓰게 하려면 파일을 지우세요.
"""
from __future__ import annotations

import gemini
from common import PLAN, SCRIPT, brand, fingerprint, load_json, load_yaml, log, mark

RULES = """[의료광고 주의 — 반드시 지킬 것]
- 효과 보장·단정 금지 (100%, 완벽, 확실히, 부작용 없음, 즉시 효과 등)
- 최상급·비교 금지 (최고, 유일, 1위, 다른 병원보다)
- 체험담·전후 비교·환자 사례 금지
- 가격·할인·이벤트·선착순 등 환자 유인 문구 금지
- 기획서와 사진에 없는 의학적 사실을 지어내지 말 것"""

PROMPT = """당신은 한국 병원 인스타그램 릴스(20~30초)의 카피라이터입니다. 아래 기획 내용으로 JSON 만 답하세요.

[후킹] 첫 2초에 스크롤을 멈추게 하는 문구 후보 3개. 각각 다른 방식:
 질문형(시청자의 고민을 짚음), 공감형(일상의 순간), 반전·호기심형(궁금증 유발).
 12~18자, 짧고 구어체. 가장 강한 단어 1~2개를 **단어** 처럼 감싸 강조 표시.
[나레이션] 4~5줄. 한 줄은 영상의 장면 하나이며 22자 이내, 말하듯 자연스럽게.
 흐름: 공감 → 시술 소개 → 특징(기획서 내용) → 마무리 안내. 줄마다 핵심 단어 1개를 **단어** 로 강조.
 마지막 줄은 문의·상담 안내 (전화번호는 쓰지 말 것 — 화면에 따로 나옴).
[캡션] 인스타 캡션 3~5줄 (이모지 1~2개 허용), [해시태그] 6~10개 (# 없이).

{rules}

[기획]
병원명: {병원명}
시술명: {시술명}
한줄소개: {한줄소개}
핵심내용: {핵심내용}
포스터 문구: {포스터문구}

{{"후킹후보": [{{"방식": "질문형", "문구": ""}}, ...], "추천후킹": 0, "나레이션": ["", ...],
  "캡션": "", "해시태그": ["", ...]}}"""

TEMPLATE = """---
상태: 초안        # 검수 후 '승인'으로 바꾸면 확정 영상이 만들어지고 발행 대기열에 오릅니다
검수자: ""
시술명: "{시술명}"
---

## 후킹
# 영상 첫 2초에 크게 나오는 문구. **단어** 는 강조 박스로 표시됩니다.
- {hook}
{alts}

## 나레이션
# 한 줄 = 장면 하나. **단어** 는 강조색으로 표시. '- ' 는 지우지 마세요.
{lines}

## 캡션
{caption}

## 해시태그
{tags}

## 검수 체크리스트
# [ ] 효과 보장·단정 표현 없음      [ ] 최상급·비교 표현 없음
# [ ] 체험담·전후 비교 없음          [ ] 할인·이벤트 유인 문구 없음(포스터 가격 표기 포함)
# [ ] 의학적 내용 의료진 확인        [ ] 오타 3회 확인
"""


def fallback(a: dict, brief: dict) -> dict:
    name = brief.get("시술명") or a.get("시술명") or "시술"
    pts = brief.get("핵심내용") or a.get("핵심내용") or []
    lines = [f"**{name}**, 어떤 시술일까요?"] + [p[:22] for p in pts[:3]] + ["자세한 상담은 **프로필 링크**에서"]
    return {"후킹후보": [{"방식": "질문형", "문구": f"**{name.split('·')[0]}**, 고민이신가요?"}], "추천후킹": 0,
            "나레이션": lines, "캡션": brief.get("한줄소개", ""), "해시태그": []}


def write(week: str, use_ai: bool = True) -> None:
    out = SCRIPT / week / "script.md"
    a = load_json(PLAN / week / "analysis.json", {})
    brief = load_yaml(PLAN / week / "brief.yml")
    fp = fingerprint(PLAN / week / "analysis.json", PLAN / week / "brief.yml")
    if out.exists():
        mark(week, "script", fp, 비고="기존 대본 유지")
        return
    if use_ai and gemini.available():
        log("후킹·대본 작성 중 (Gemini)…")
        b = brand()
        try:
            d = gemini.ask_json(PROMPT.format(rules=RULES, 병원명=b.get("병원명", ""), 시술명=brief.get("시술명", ""),
                                              한줄소개=brief.get("한줄소개", ""), 핵심내용=brief.get("핵심내용", []),
                                              포스터문구=a.get("포스터문구", [])))
        except RuntimeError as e:
            log(f"대본 자동 작성 실패 → 기본 틀로 작성: {e}")
            d = fallback(a, brief)
    else:
        d = fallback(a, brief)
    hooks = d.get("후킹후보") or [{"문구": ""}]
    pick = min(int(d.get("추천후킹", 0) or 0), len(hooks) - 1)
    alts = "\n".join(f"> 후보({h.get('방식', '')}): {h.get('문구', '')}" for i, h in enumerate(hooks) if i != pick)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.format(
        시술명=brief.get("시술명", ""), hook=hooks[pick].get("문구", ""),
        alts=("# 다른 후보 — 바꾸려면 위 '- ' 줄의 문구와 교체\n" + alts) if alts else "",
        lines="\n".join(f"- {l}" for l in d.get("나레이션", [])),
        caption=d.get("캡션", "").strip(), tags=" ".join(f"#{t.lstrip('#')}" for t in d.get("해시태그", []))),
        encoding="utf-8")
    mark(week, "script", fp)
    log(f"대본 작성 → {out.relative_to(SCRIPT.parent)}")
