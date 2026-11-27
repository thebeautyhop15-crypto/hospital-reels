"""01_plan/<주차>/brief.yml → 02_script/<주차>/script.md (AI 대본 초안).

Gemini API 무료 등급을 사용합니다. 환경변수:
  GEMINI_API_KEY  (필수, GitHub Secrets에 등록)
  GEMINI_MODEL    (선택, 기본 gemini-2.5-flash)

사용: python scripts/draft_script.py 2026-W49
      python scripts/draft_script.py --all   # 대본이 없는 모든 주차
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from common import PLAN_DIR, SCRIPT_DIR, load_brand, load_brief

PROMPT = """당신은 한국 병원의 SNS 릴스(30초 내외) 나레이션 작가입니다.
아래 기획서를 바탕으로 나레이션 대본을 작성하세요.

[형식]
- 5~7줄. 한 줄은 영상의 장면 하나이며 25자 이내.
- 첫 줄은 시청자의 고민을 짚는 질문 또는 공감 문장.
- 마지막 줄은 행동유도 문구를 그대로 사용.
- 각 줄을 '- '로 시작하고, 다른 설명·제목·이모지는 쓰지 마세요.

[의료광고 주의 — 반드시 지킬 것]
- 효과를 보장하거나 단정하는 표현 금지 (예: 100%, 완벽, 확실히, 부작용 없음)
- 최상급·비교 표현 금지 (예: 최고, 유일, 1위, 다른 병원보다)
- 환자 체험담·전후 비교 표현 금지
- 가격 할인·이벤트 등 환자 유인 표현 금지
- 기획서에 없는 의학적 사실을 지어내지 말 것

[기획서]
병원명: {병원명}
시술명: {시술명}
한줄소개: {한줄소개}
핵심내용:
{핵심내용}
행동유도: {행동유도}
"""

TEMPLATE = """---
상태: 초안        # 검수 후 '승인'으로 바꿔야 영상이 만들어집니다
검수자: ""        # 검수한 사람 이름
시술명: "{시술명}"
---

# 나레이션
# 한 줄 = 장면 하나(포스터 장면과 문구 장면이 번갈아 자동 배치). 문구를 직접 고쳐도 됩니다.
# 줄 앞의 '- '는 지우지 마세요. 장면을 직접 정하려면 문장 앞에 [문구], [포스터 줌인] 등을 붙입니다(README 참고).
{lines}

# 검수 체크리스트
# [ ] 효과 보장·단정 표현 없음
# [ ] 최상급·비교 표현 없음
# [ ] 체험담·전후 비교 없음
# [ ] 할인·이벤트 유인 문구 없음
# [ ] 의학적 내용 의료진 확인 완료
# [ ] 오타 3회 확인
"""


def call_gemini(prompt: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 환경변수가 없습니다. GitHub Secrets에 등록하세요.")
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])


def draft(week: str) -> bool:
    out = SCRIPT_DIR / week / "script.md"
    if out.exists():
        print(f"[건너뜀] {week}: 대본이 이미 있습니다 ({out.relative_to(SCRIPT_DIR.parent)})")
        return False
    brief = load_brief(week)
    brand = load_brand()
    points = "\n".join(f"- {p}" for p in brief.get("핵심내용") or [])
    prompt = PROMPT.format(
        병원명=brand.get("병원명", ""),
        시술명=brief.get("시술명", ""),
        한줄소개=brief.get("한줄소개", ""),
        핵심내용=points,
        행동유도=brief.get("행동유도", ""),
    )
    text = call_gemini(prompt)
    lines = [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]
    if not lines:
        sys.exit(f"AI 응답에서 대본 줄을 찾지 못했습니다:\n{text}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.format(시술명=brief.get("시술명", ""), lines="\n".join(lines)), encoding="utf-8")
    print(f"[완료] {out.relative_to(SCRIPT_DIR.parent)} — 검수 후 상태를 '승인'으로 바꾸세요.")
    return True


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    weeks = sorted(p.parent.name for p in PLAN_DIR.glob("*/brief.yml")) if args[0] == "--all" else args
    for w in weeks:
        draft(w)


if __name__ == "__main__":
    main()
