"""승인된 대본 + 포스터 이미지 → 9:16 릴스 영상 (Edge TTS + FFmpeg, 전부 무료).

입력
  02_script/<주차>/script.md   상태: 승인 인 대본
  03_design/<주차>/*.jpg|png  포스터 이미지 1장 이상 (파일명 순서대로 사용)
  03_design/_common/            로고·배경음악·글꼴 (선택)
출력
  04_output/<주차>/reel.mp4, reel.srt, cover.jpg

사용
  python scripts/build_reel.py              # 이번 주(한국 시간)
  python scripts/build_reel.py 2026-W49
  python scripts/build_reel.py 2026-W49 --dry-tts   # 음성 없이 무음으로 테스트
  python scripts/build_reel.py 2026-W49 --force     # 승인 전이라도 미리보기 제작
  python scripts/build_reel.py --pending            # 승인됐는데 아직 안 만든 주차 전부
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from common import (APPROVED, COMMON_DESIGN_DIR, DESIGN_DIR, OUTPUT_DIR, PLAN_DIR, ROOT,
                    SCRIPT_DIR, current_week, load_brand, load_brief, parse_script)

W, H, FPS = 1080, 1920, 30
GAP = 0.25          # 문장 사이 쉼(초)
TAIL = 0.4          # 마지막 문장 뒤 여유(초)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
DEFAULT_FONT_FAMILY = "Noto Sans CJK KR"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"명령 실패: {' '.join(cmd[:6])} ...\n{r.stderr[-3000:]}")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def color(hex_str: str) -> str:
    return "0x" + hex_str.strip().lstrip("#")


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------- 1. 음성 ----------
async def tts_line(text: str, voice: str, rate: str, out: Path) -> None:
    import edge_tts  # 지연 import: --dry-tts 때는 필요 없음
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def make_voice(lines: list[str], brand: dict, tmp: Path, dry: bool) -> tuple[Path, list[tuple[float, float, str]]]:
    """문장별 음성을 만들어 이어 붙이고, (시작, 끝, 문장) 목록을 돌려줍니다."""
    parts: list[Path] = []
    cues: list[tuple[float, float, str]] = []
    t = 0.0
    gap = tmp / "gap.wav"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(GAP), gap.as_posix()])
    for i, line in enumerate(lines):
        raw = tmp / f"line{i:02d}.mp3"
        wav = tmp / f"line{i:02d}.wav"
        if dry:
            d = max(1.2, len(line) * 0.17)
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{d:.2f}", wav.as_posix()])
        else:
            asyncio.run(tts_line(line, brand.get("음성", "ko-KR-SunHiNeural"),
                                 brand.get("말하기속도", "+0%"), raw))
            run(["ffmpeg", "-y", "-i", raw.as_posix(), "-ar", "48000", "-ac", "1", wav.as_posix()])
        d = duration(wav)
        cues.append((t, t + d, line))
        parts += [wav, gap]
        t += d + GAP
    lst = tmp / "voice.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    voice = tmp / "voice.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-af", f"apad=pad_dur={TAIL}",
         voice.name], cwd=tmp)
    return voice, cues


# ---------- 2. 화면 ----------
def text_file(tmp: Path, name: str, text: str) -> str:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p.name


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def make_ass(cues: list[tuple[float, float, str]], offset: float, family: str) -> str:
    """화면 크기(1080x1920) 기준으로 고정한 자막 파일. 하단 중앙, 반투명 검정 박스."""
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{family},64,&H00FFFFFF,&H00FFFFFF,&H99000000,&H99000000,"
        "-1,0,0,0,100,100,0,0,3,18,0,2,90,90,460,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    body = "".join(f"Dialogue: 0,{ass_time(s + offset)},{ass_time(e + offset)},Default,,0,0,0,,{t}\n"
                   for s, e, t in cues)
    return head + body


def card(tmp: Path, name: str, dur: float, brand: dict, texts: list[tuple[str, int, int, str, int]],
         font: str, logo: Path | None, accent_y: int | None) -> Path:
    """단색 배경 카드. texts = [(문구, 글자크기, y위치, 색, 줄당 글자수)]"""
    out = tmp / f"{name}.mp4"
    vf = []
    if accent_y is not None:
        vf.append(f"drawbox=x={W//2-90}:y={accent_y}:w=180:h=8:color={color(brand.get('강조색', '#F2C14E'))}:t=fill")
    for i, (txt, size, y, col, wrap) in enumerate(texts):
        if not txt:
            continue
        # '|' 는 직접 줄바꿈, 나머지는 자동 줄바꿈. 줄마다 따로 가운데 정렬
        wrapped = [w for part in str(txt).split("|")
                   for w in textwrap.wrap(part.strip(), width=wrap, break_long_words=False)]
        for j, line in enumerate(wrapped):
            tf = text_file(tmp, f"{name}_t{i}_{j}.txt", line)
            vf.append(f"drawtext=fontfile='{font}':textfile={tf}:fontsize={size}:fontcolor={col}"
                      f":x=(w-text_w)/2:y={y + j * int(size * 1.45)}")
    vf.append(f"fade=t=in:st=0:d=0.3,fade=t=out:st={dur-0.3:.2f}:d=0.3,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color(brand.get('메인색', '#1F3A5F'))}:s={W}x{H}:r={FPS}:d={dur}"]
    filt = ",".join(vf)
    if logo:
        cmd += ["-i", logo.as_posix()]
        filt = f"[0:v]{filt}[bg];[1:v]scale=360:-1[lg];[bg][lg]overlay=(W-w)/2:220[v]"
        cmd += ["-filter_complex", filt, "-map", "[v]"]
    else:
        cmd += ["-vf", filt]
    cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", out.name]
    run(cmd, cwd=tmp)
    return out


def poster_clip(tmp: Path, idx: int, img: Path, dur: float) -> Path:
    """포스터 1장 → 흐린 배경 + 원본 + 천천히 확대/축소."""
    out = tmp / f"poster{idx:02d}.mp4"
    frames = max(1, round(dur * FPS))
    z = f"1+0.06*on/{frames}" if idx % 2 == 0 else f"1.06-0.06*on/{frames}"
    filt = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
        f"eq=brightness=-0.12[bg];"
        f"[0:v]scale={W-80}:{H-160}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,scale={W*2}:{H*2},"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        f"fade=t=in:st=0:d=0.25,fade=t=out:st={max(0.0, dur-0.25):.2f}:d=0.25,format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-i", img.as_posix(), "-filter_complex", filt, "-map", "[v]",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out.name], cwd=tmp)
    return out


# ---------- 3. 합치기 ----------
def font_family(font_path: str) -> str:
    if font_path == DEFAULT_FONT:
        return DEFAULT_FONT_FAMILY
    r = subprocess.run(["fc-scan", "--format", "%{family[0]}\n", font_path], capture_output=True, text=True)
    first = r.stdout.strip().splitlines()
    return first[0] if first else DEFAULT_FONT_FAMILY


def build(week: str, dry: bool, force: bool) -> Path:
    brand = load_brand()
    brief = load_brief(week)
    script_path = SCRIPT_DIR / week / "script.md"
    if not script_path.exists():
        sys.exit(f"대본이 없습니다: {script_path}")
    meta, lines = parse_script(script_path)
    if str(meta.get("상태", "")).strip() != APPROVED and not force:
        sys.exit(f"[중단] {week} 대본이 아직 '{APPROVED}' 상태가 아닙니다 (현재: {meta.get('상태')}).")
    if not lines:
        sys.exit("대본에서 나레이션 줄('- '로 시작)을 찾지 못했습니다.")
    images = sorted(p for p in (DESIGN_DIR / week).glob("*") if p.suffix.lower() in IMG_EXT)
    if not images:
        sys.exit(f"포스터 이미지가 없습니다: {DESIGN_DIR / week}")

    custom_font = brand.get("글꼴파일") or ""
    font = (COMMON_DESIGN_DIR / custom_font).as_posix() if custom_font else DEFAULT_FONT
    logo = COMMON_DESIGN_DIR / brand["로고파일"] if brand.get("로고파일") else None
    bgm = COMMON_DESIGN_DIR / brand["배경음악파일"] if brand.get("배경음악파일") else None
    intro_d = float(brand.get("오프닝길이", 2.0))
    outro_d = float(brand.get("엔딩길이", 3.0))
    fg = brand.get("글자색", "#FFFFFF").replace("#", "0x")
    sub_fg = "0xD9DEE6"

    out_dir = OUTPUT_DIR / week
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        voice, cues = make_voice(lines, brand, tmp, dry)
        narr = duration(voice)

        # 오프닝 · 포스터 · 엔딩 화면
        intro = card(tmp, "intro", intro_d, brand, [
            (brand.get("병원명", ""), 52, 760, sub_fg, 20),
            (brief.get("시술명", ""), 104, 860, fg, 9),
        ], font, logo, accent_y=1120)
        per = narr / len(images)
        clips = [poster_clip(tmp, i, img, per) for i, img in enumerate(images)]
        review = f"의료광고심의 {brief['심의번호']}" if brief.get("심의번호") else ""
        outro = card(tmp, "outro", outro_d, brand, [
            (brand.get("병원명", ""), 96, 720, fg, 10),
            (brand.get("연락처", ""), 56, 900, fg, 20),
            (brand.get("주소", ""), 40, 990, sub_fg, 26),
            # 릴스 하단 약 25%는 인스타 버튼·캡션에 가려지므로 그 위에 배치
            (brand.get("고지문", ""), 36, 1240, sub_fg, 26),
            (review, 32, 1400, sub_fg, 30),
        ], font, logo, accent_y=None)

        lst = tmp / "video.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in [intro, *clips, outro]), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "silent.mp4"], cwd=tmp)
        total = duration(tmp / "silent.mp4")

        # 자막 (오프닝 길이만큼 뒤로)
        srt = "".join(f"{i}\n{srt_time(s + intro_d)} --> {srt_time(e + intro_d)}\n{t}\n\n"
                      for i, (s, e, t) in enumerate(cues, 1))
        (out_dir / "reel.srt").write_text(srt, encoding="utf-8")   # 업로드·보관용
        fonts_dir = tmp / "fonts"
        fonts_dir.mkdir()
        shutil.copy(font, fonts_dir)
        (tmp / "subs.ass").write_text(make_ass(cues, intro_d, font_family(font)), encoding="utf-8")

        # 음성 + 배경음악
        delay = int(intro_d * 1000)
        cmd = ["ffmpeg", "-y", "-i", "silent.mp4", "-i", voice.name]
        afilt = f"[1:a]adelay={delay}|{delay},apad[a1]"
        amap = "[a1]"
        if bgm and bgm.exists():
            cmd += ["-stream_loop", "-1", "-i", bgm.as_posix()]
            vol = float(brand.get("배경음악볼륨", 0.12))
            afilt += (f";[2:a]volume={vol},afade=t=out:st={max(0.0, total-1.5):.2f}:d=1.5[a2];"
                      f"[a1][a2]amix=inputs=2:duration=first:normalize=0[am]")
            amap = "[am]"
        filt = f"[0:v]ass=subs.ass:fontsdir=fonts[v];{afilt}"
        final = out_dir / "reel.mp4"
        cmd += ["-filter_complex", filt, "-map", "[v]", "-map", amap, "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart", final.as_posix()]
        run(cmd, cwd=tmp)

        # 커버 이미지 (첫 포스터 장면)
        run(["ffmpeg", "-y", "-ss", f"{intro_d + 0.6:.2f}", "-i", final.as_posix(), "-frames:v", "1",
             "-q:v", "2", (out_dir / "cover.jpg").as_posix()])

    print(f"[완료] {final.relative_to(OUTPUT_DIR.parent)}  길이 {duration(final):.1f}초")
    return final


def fingerprint(week: str) -> str:
    """대본·포스터·공통설정이 바뀌었는지 판단하는 지문."""
    h = hashlib.sha256()
    files = [SCRIPT_DIR / week / "script.md", ROOT / "config" / "brand.yml", PLAN_DIR / week / "brief.yml"]
    files += sorted(p for p in (DESIGN_DIR / week).glob("*") if p.is_file())
    files += sorted(p for p in COMMON_DESIGN_DIR.glob("*") if p.is_file())
    for p in files:
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def pending_weeks() -> list[str]:
    """승인됐는데 아직 영상이 없거나, 승인 후 내용이 바뀐 주차."""
    weeks = []
    for sp in sorted(SCRIPT_DIR.glob("*/script.md")):
        week = sp.parent.name
        meta, _ = parse_script(sp)
        if str(meta.get("상태", "")).strip() != APPROVED:
            continue
        stamp = OUTPUT_DIR / week / "build.json"
        old = json.loads(stamp.read_text(encoding="utf-8")).get("fingerprint") if stamp.exists() else None
        if old != fingerprint(week):
            weeks.append(week)
    return weeks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("week", nargs="?", default=None)
    ap.add_argument("--pending", action="store_true", help="승인됐지만 아직 안 만든 주차를 모두 제작")
    ap.add_argument("--dry-tts", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    weeks = pending_weeks() if a.pending else [a.week or current_week()]
    if not weeks:
        print("제작할 주차가 없습니다 (승인된 새 대본 없음).")
    for week in weeks:
        build(week, a.dry_tts, a.force)
        stamp = {"fingerprint": fingerprint(week), "voice": "dry" if a.dry_tts else "edge-tts"}
        (OUTPUT_DIR / week / "build.json").write_text(json.dumps(stamp, ensure_ascii=False, indent=2),
                                                      encoding="utf-8")


if __name__ == "__main__":
    main()
