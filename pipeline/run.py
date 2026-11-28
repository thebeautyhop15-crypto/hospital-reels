"""전체 실행기 — 사진만 넣으면 기획 → 대본 → 시나리오 → 음향 → 영상 → 품질점검 → 발행대기열까지 차례로.

  python pipeline/run.py                 00_inbox 사진 처리 + 앞 단계가 바뀐 모든 주차 다시 제작
  python pipeline/run.py --week 2026-W49 한 주차만
  python pipeline/run.py --final         승인 전이라도 확정본으로 제작(시험용)
  python pipeline/run.py --no-ai         Gemini 없이(사진 분석·대본 자동 작성 생략)
  python pipeline/run.py --publish [--go] 발행할 차례인 주차를 인스타그램에 (--go 없으면 점검만)
각 단계는 앞 단계 결과가 바뀌었을 때만 다시 만들어집니다 (01_plan/<주차>/manifest.yml).
"""
from __future__ import annotations

import argparse
import sys

import qa
import stage_audio
import stage_plan
import stage_publish
import stage_scenario
import stage_script
import stage_video
from common import PLAN, PUBLISH, VIDEO, images, load_yaml, log


def weeks() -> list[str]:
    return sorted(p.name for p in PLAN.iterdir() if p.is_dir() and images(p.name))


def produce(week: str, use_ai: bool, final: bool) -> bool:
    if load_yaml(PUBLISH / week / "post.yml").get("상태") == "발행완료":
        return True                                         # 이미 발행한 주차는 다시 만들지 않음
    log(f"===== {week} =====")
    stage_plan.analyze(week, use_ai)
    stage_script.write(week, use_ai)
    sc = stage_scenario.build(week)["장면"]
    tl = stage_audio.build(week, sc)
    video = stage_video.build(week, sc, tl, force_final=final)
    ok = qa.check(video, tl["음성"] == "실제") if video else "확인필요" not in _qa_text(week)
    stage_publish.queue(week)
    return ok


def _qa_text(week: str) -> str:
    p = VIDEO / week / "qa.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    if a.publish:
        for w in ([a.week] if a.week else stage_publish.due()) or []:
            stage_publish.publish(w, check_only=not a.go)
        if not a.week and not stage_publish.due():
            log("발행할 차례인 영상이 없습니다.")
        return
    new = stage_plan.ingest_inbox()
    todo = [a.week] if a.week else (weeks() if not new else sorted(set(weeks()) | {new}))
    bad = [w for w in todo if not produce(w, not a.no_ai, a.final)]
    if bad:
        log(f"품질 점검 확인필요: {', '.join(bad)} (05_video/<주차>/qa.txt)")


if __name__ == "__main__":
    sys.exit(main())
