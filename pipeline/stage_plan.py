"""1단계 기획 — 00_inbox 의 사진을 주차 폴더로 옮기고, 사진을 읽어 analysis.json·brief.yml 을 만듦.

analysis.json (사진 분석 결과 — 다음 단계들이 사용)
  병원명, 시술명, 제목문구, 핵심내용[], 포스터문구[], 가격표기, 이벤트문구, 추천테마,
  영역[]: {이름, 종류(제목|카드|얼굴|가격|아이콘|연락처), 이미지(1부터), box[x0,y0,x1,y1](0~1)}
"""
from __future__ import annotations

import shutil

import gemini
from common import (IMG_EXT, INBOX, PLAN, brand, fingerprint, images, load_json, log, mark,
                    next_free_week, save_json, save_yaml, stage_done)

PROMPT = """당신은 병원 마케팅 디자이너입니다. 첨부한 병원 홍보 이미지(포스터·사진)를 꼼꼼히 읽고 JSON 으로만 답하세요.
이미지에 실제로 적힌 내용만 쓰고, 없는 정보는 빈 값으로 두세요. 추측해서 지어내지 마세요.

{
 "병원명": "이미지에 적힌 병원 이름",
 "시술명": "대표 시술 이름 (여러 개면 '·'로 연결)",
 "제목문구": "가장 큰 제목 문구",
 "부제문구": "제목 주변 보조 문구",
 "핵심내용": ["시술별 설명 문장을 이미지 표현 그대로 3~5개"],
 "포스터문구": ["이미지의 모든 글자를 줄 단위로"],
 "가격표기": true/false,
 "이벤트문구": true/false,
 "추천테마": "럭셔리 | 임팩트 | 부드러움 중 이미지 분위기에 맞는 것",
 "영역": [
   {"이름": "짧은 이름 (예: 눈밑 리프팅 카드)", "종류": "제목|카드|얼굴|가격|아이콘|연락처",
    "이미지": 1, "box_2d": [ymin, xmin, ymax, xmax]}
 ]
}
영역의 box_2d 는 각 이미지 기준 0~1000 정수 좌표입니다. '카드'는 시술 이름·설명·가격이 한 덩어리로 묶인 부분이며,
글자가 잘리지 않도록 넉넉하게 잡으세요. 영역은 최대 8개.
"""


def ingest_inbox() -> str | None:
    """00_inbox 의 사진 → 01_plan/<주차>/source/. 하위 폴더 이름이 주차(예: 2026-W50)면 그 주차를 사용."""
    files = sorted(p for p in INBOX.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXT)
    if not files:
        return None
    sub = files[0].parent.name if files[0].parent != INBOX else ""
    week = sub if sub[:4].isdigit() and "-W" in sub else next_free_week()
    dst = PLAN / week / "source"
    dst.mkdir(parents=True, exist_ok=True)
    start = len(list(dst.glob("*")))
    for i, f in enumerate(files, start=start + 1):
        shutil.move(str(f), dst / f"poster_{i:02d}{f.suffix.lower()}")
    for d in sorted((p for p in INBOX.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    log(f"사진 {len(files)}장 → {dst.relative_to(PLAN.parent)} ({week})")
    return week


def analyze(week: str, use_ai: bool = True) -> dict:
    src = PLAN / week / "source"
    out = PLAN / week / "analysis.json"
    fp = fingerprint(src)
    if out.exists() and stage_done(week, "plan", fp):
        return load_json(out)
    imgs = images(week)
    if not imgs:
        raise SystemExit(f"{week}: 사진이 없습니다 ({src})")
    old = load_json(out, {})
    if old and (not use_ai or old.get("수동")):
        data = old                                      # 사람이 확인한 분석('수동': true)은 AI 가 덮어쓰지 않음
    elif use_ai and gemini.available():
        log("사진 분석 중 (Gemini)…")
        try:
            data = gemini.ask_json(PROMPT, imgs)
        except RuntimeError as e:
            log(f"사진 분석 실패 → 기본 연출로 진행: {e}")
            data = {"병원명": brand().get("병원명", ""), "시술명": "", "핵심내용": [], "영역": []}
        for r in data.get("영역", []):
            b = r.pop("box_2d", None)
            if b and len(b) == 4:
                y0, x0, y1, x1 = [v / 1000 for v in b]
                r["box"] = [round(max(0, x0 - 0.01), 3), round(max(0, y0 - 0.01), 3),
                            round(min(1, x1 + 0.01), 3), round(min(1, y1 + 0.01), 3)]
    else:
        log("AI 없이 진행: 영역 정보 없이 기본 연출을 사용합니다.")
        data = old or {"병원명": brand().get("병원명", ""), "시술명": "", "핵심내용": [], "영역": []}
    data["이미지수"] = len(imgs)
    save_json(out, data)
    make_brief(week, data)
    mark(week, "plan", fp, 이미지=[p.name for p in imgs])
    return data


def make_brief(week: str, a: dict) -> None:
    """brief.yml 이 없을 때만 분석 결과로 작성 (사람이 고친 기획서는 덮어쓰지 않음)."""
    p = PLAN / week / "brief.yml"
    if p.exists():
        return
    b = brand()
    save_yaml(p, {
        "시술명": a.get("시술명", ""),
        "한줄소개": a.get("부제문구") or a.get("제목문구", ""),
        "핵심내용": a.get("핵심내용", [])[:5],
        "행동유도": b.get("행동유도", ""),
        "발행예정일": "",
        "심의번호": "",
        "해시태그": [],
        "참고": "가격 표기가 있는 포스터입니다 — 의료광고 심의 확인 필요" if a.get("가격표기") else "",
    }, f"# {week} 기획서 — 사진 분석으로 자동 작성. 고치면 다음 단계가 이 내용을 사용합니다.")
