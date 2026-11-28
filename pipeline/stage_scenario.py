"""3단계 시나리오 — 대본 + 사진 분석 → 03_scenario/<주차>/scenario.yml (장면별 연출표)

장면 항목
  종류     : 후킹 | 포스터 | 패널 | 문구 | 문의 | 엔딩
  대사     : 화면 글자 + 음성 (**강조**, | 줄바꿈)
  이미지   : 사용할 사진 번호 (1부터)
  영역     : [x0, y0, x1, y1] 사진 안 위치(0~1) — 패널은 이 부분을 카드로 잘라내고, 포스터·후킹은 이 부분으로 다가감
  움직임   : 줌인 | 줌아웃 | 아래로 | 위로 | 오른쪽 | 왼쪽 | 대각선 | 초점 | 전체공개 | 펀치줌
  글자효과 : 떠오름 | 팝 | 타자 | 마스크   (문구 장면)
  라벨     : 패널 카드 위 작은 이름표
  전환     : 다음 장면으로 넘어갈 때 효과 (비우면 style.yml 목록에서 차례로)
'자동생성: true' 인 동안은 대본이 바뀌면 다시 만들어집니다. 직접 고쳤다면 false 로 바꾸세요(덮어쓰지 않음).
"""
from __future__ import annotations

import re

from common import (PLAN, SCENARIO, SCRIPT, fingerprint, load_json, load_yaml, log, mark, parse_script, save_yaml,
                    stage_done, strip_marks, style)

CTA = re.compile(r"상담|문의|예약|링크|전화")
MOVES = ["아래로", "대각선", "오른쪽", "초점", "위로", "왼쪽"]
TEXT_FX = ["떠오름", "마스크", "팝", "타자"]


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[가-힣A-Za-z0-9.]+", strip_marks(s)) if len(t) >= 2}


def _match(line: str, regions: list[dict]) -> dict | None:
    """대사와 이름이 가장 많이 겹치는 카드 영역."""
    best, score = None, 0
    words = _tokens(line)
    for r in regions:
        n = len(words & _tokens(r.get("이름", "")))
        n += sum(1 for w in words for v in _tokens(r.get("이름", "")) if w != v and (w in v or v in w)) * 0.5
        if n > score:
            best, score = r, n
    return best if score >= 1 else None


def plan_scenes(script: dict, analysis: dict, n_images: int) -> list[dict]:
    regions = analysis.get("영역", [])
    cards = [r for r in regions if r.get("종류") == "카드" and r.get("box")]
    title = next((r for r in regions if r.get("종류") == "제목" and r.get("box")), None)
    face = next((r for r in regions if r.get("종류") == "얼굴" and r.get("box")), None)
    lines = script["lines"]
    scenes = []
    hook_focus = face or title
    scenes.append({"종류": "후킹", "대사": script["hook"], "이미지": (hook_focus or {}).get("이미지", 1),
                   **({"영역": hook_focus["box"]} if hook_focus else {})})
    used, mv, fx = set(), 0, 0
    for i, line in enumerate(lines):
        last = i == len(lines) - 1
        if last and CTA.search(strip_marks(line)):
            scenes.append({"종류": "문의", "대사": line, "이미지": 1})
            continue
        free = [c for c in cards if id(c) not in used]
        card = _match(line, free)
        if card is None and free and i > 0 and len(lines) - i - 1 <= len(free):   # 남은 줄 수만큼 카드가 남았으면 차례로
            card = free[0]
        if card:
            used.add(id(card))
            scenes.append({"종류": "패널", "대사": line, "이미지": card.get("이미지", 1), "영역": card["box"],
                           "라벨": card.get("이름", "")})
        elif i == 0:
            sc = {"종류": "포스터", "대사": line, "이미지": 1, "움직임": "줌인"}
            if title:
                sc["영역"] = title["box"]
            scenes.append(sc)
        elif scenes[-1]["종류"] in ("문구",):
            scenes.append({"종류": "포스터", "대사": line, "이미지": (i % max(1, n_images)) + 1,
                           "움직임": MOVES[mv % len(MOVES)]})
            mv += 1
        else:
            scenes.append({"종류": "문구", "대사": line, "이미지": (i % max(1, n_images)) + 1,
                           "글자효과": TEXT_FX[fx % len(TEXT_FX)]})
            fx += 1
    scenes.append({"종류": "엔딩"})
    trans = style().get("효과", {}).get("전환목록", ["fade"])
    for k, sc in enumerate(scenes[:-1]):
        sc["전환"] = trans[k % len(trans)]
    return scenes


def build(week: str) -> dict:
    sp = SCRIPT / week / "script.md"
    out = SCENARIO / week / "scenario.yml"
    fp = fingerprint(sp, PLAN / week / "analysis.json")
    cur = load_yaml(out, {})
    if cur and (cur.get("자동생성") is False or stage_done(week, "scenario", fp)):
        if cur.get("자동생성") is False:
            log("시나리오: 직접 수정본 사용 (자동생성: false)")
        mark(week, "scenario", fp)
        return cur
    script = parse_script(sp)
    if not script["hook"] or not script["lines"]:
        raise SystemExit(f"{sp}: '## 후킹' 과 '## 나레이션' 내용이 필요합니다.")
    a = load_json(PLAN / week / "analysis.json", {})
    data = {"자동생성": True, "주차": week, "장면": plan_scenes(script, a, int(a.get("이미지수", 1) or 1))}
    save_yaml(out, data, "# 장면 연출표 — 설명은 pipeline/stage_scenario.py 맨 위 참고. 직접 고치면 자동생성: false 로.")
    mark(week, "scenario", fp, 장면수=len(data["장면"]))
    log(f"시나리오 {len(data['장면'])}장면 → {out.relative_to(SCENARIO.parent)}")
    return data

