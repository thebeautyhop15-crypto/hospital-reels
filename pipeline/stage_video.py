"""5단계 영상 — 시나리오 + 음향 시간표로 장면을 그리고 전환 효과로 이어 붙임 → 05_video/<주차>/

  preview.mp4   대본이 '승인' 전일 때 (화면 위에 '검수용 미리보기' 표시 — 발행되지 않음)
  reel.mp4      대본이 '승인' 이면 확정본 (발행 단계가 사용)
  cover.jpg     표지(후킹 장면), reel.srt 자막 파일, build.json 제작 정보
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import renderer
from common import (APPROVED, AUDIO, FPS, PLAN, SCENARIO, SCRIPT, VIDEO, brand, fingerprint, font, images, log, mark,
                    now, parse_script, run, save_json, stage_done, style, CONFIG, FONTS)

ROLES = ["후킹", "제목", "본문", "자막", "강조", "보조"]


def fonts() -> dict:
    t = style()["현재테마"]
    return {r: (font(r), int((t.get(r) or {}).get("크기", 60))) for r in ROLES}


def srt(tl: dict, path: Path) -> None:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    rows, n = [], 1
    for it in tl["장면"]:
        if it["words"]:
            s = it["start"] + it["words"][0]["start"]
            e = it["start"] + it["words"][-1]["end"] + 0.2
            rows.append(f"{n}\n{ts(s)} --> {ts(e)}\n{' '.join(w['text'] for w in it['words'])}\n")
            n += 1
    path.write_text("\n".join(rows), encoding="utf-8")


def build(week: str, scenes: list[dict], tl: dict, force_final: bool = False) -> Path:
    meta = parse_script(SCRIPT / week / "script.md")["meta"]
    final = force_final or str(meta.get("상태", "")).strip() == APPROVED
    out = VIDEO / week
    out.mkdir(parents=True, exist_ok=True)
    name = "reel.mp4" if final else "preview.mp4"
    fp = fingerprint(SCENARIO / week / "scenario.yml", AUDIO / week / "timeline.json", AUDIO / week / "mix.m4a",
                     PLAN / week / "source", CONFIG, FONTS) + ("F" if final else "P")
    if stage_done(week, "video", fp) and (out / name).exists():
        log(f"영상: 바뀐 것 없음 → {name} 유지")
        return out / name
    imgs = images(week)
    ctx = renderer.Ctx(brand=brand(), style=style(), fonts=fonts(), images=imgs, seed=sum(map(ord, week)))
    ctx.preview = not final
    ctx.prepare()
    T = tl["전환길이"]
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (sc, it) in enumerate(zip(scenes, tl["장면"])):
            s = dict(sc)
            s["이미지"] = min(len(imgs), max(1, int(s.get("이미지", 1)))) - 1
            words = renderer.split_words(s.get("대사", "")) if s.get("대사") else []
            for w, t in zip(words, it["words"]):
                w.start, w.end = t["start"], t["end"]
            s["_words"] = words
            if s["종류"] == "엔딩":
                s.setdefault("심의번호", _review_no(week))
            p = Path(td) / f"s{i:02d}.mp4"
            renderer.render(ctx, s, it["dur"], p)
            parts.append(p)
            log(f"  장면 {i + 1}/{len(scenes)} {s['종류']} {it['dur']:.1f}초")
        inputs, filt, prev = [], [], "[0:v]"
        for p in parts:
            inputs += ["-i", str(p)]
        for i in range(1, len(parts)):
            tr = scenes[i - 1].get("전환") or "fade"
            off = tl["장면"][i]["start"]
            filt.append(f"{prev}[{i}:v]xfade=transition={tr}:duration={T}:offset={off:.3f}[x{i}]")
            prev = f"[x{i}]"
        filt.append(f"{prev}format=yuv420p,fps={FPS}[v]")
        tmp = Path(td) / name
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-i", str(AUDIO / week / "mix.m4a"),
             "-filter_complex", ";".join(filt), "-map", "[v]", "-map", f"{len(parts)}:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{tl['길이']:.3f}",
             "-movflags", "+faststart", str(tmp)])
        shutil.move(str(tmp), out / name)
    if final and (out / "preview.mp4").exists():
        (out / "preview.mp4").unlink()
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.3", "-i", str(out / name), "-frames:v", "1", "-q:v", "2",
         str(out / "cover.jpg")])
    srt(tl, out / "reel.srt")
    save_json(out / "build.json", {"주차": week, "파일": name, "확정": final, "길이": tl["길이"], "음성": tl["음성"],
                                   "테마": style()["현재테마이름"], "장면": [s["종류"] for s in scenes],
                                   "제작": now().isoformat(timespec="seconds")})
    mark(week, "video", fp, 파일=name)
    log(f"영상 완료 → {(out / name).relative_to(VIDEO.parent)}")
    return out / name


def _review_no(week: str) -> str:
    from common import load_yaml
    return str(load_yaml(PLAN / week / "brief.yml").get("심의번호", "") or "")
