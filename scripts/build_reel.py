"""승인된 대본 + 포스터 이미지 → 9:16 릴스 영상 (Edge TTS + FFmpeg, 전부 무료).

장면 구성
  오프닝(움직이는 제목) → 대본 한 줄마다 장면 1개 → 엔딩(연락처·고지문)
  장면 종류
    · 포스터 장면: 줌인/줌아웃/위아래·좌우 이동/특정 부분 확대 + 빛 반사 + 하단 자막
    · 문구 장면 : 흐린 포스터 + 브랜드색 배경 위에 큰 글씨가 아래에서 떠오름
  장면 사이: 부드러운 전환 효과(밀기·원형·확대·디졸브 등)를 번갈아 사용
  모든 글자는 인스타·페이스북 릴스 안전영역(위 14%, 아래 35%, 좌우 6% 제외) 안에 배치

대본 한 줄에 장면을 직접 지정할 수 있습니다 (생략하면 자동 배치).
  - [문구] 탄력은 다르게, 더 깊이
  - [포스터 줌인] 시간이 지나도 변하지 않는 아름다움
  - [포스터 아래로] ...      (위로 / 왼쪽 / 오른쪽 / 줌아웃)
  - [포스터 확대 0.35 0.52 1.5] ...   (포스터 기준 가로 위치, 세로 위치(0~1), 배율)
  - [포스터 패널 0.04 0.42 0.70 0.62] ...  (포스터의 해당 영역(왼쪽 위 x y, 오른쪽 아래 x y)을 카드처럼 띄워 강조)
  - [포스터2 줌인] ...       (두 번째 이미지 사용)

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
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from common import (APPROVED, COMMON_DESIGN_DIR, DESIGN_DIR, OUTPUT_DIR, PLAN_DIR, ROOT,
                    SCRIPT_DIR, current_week, load_brand, load_brief, parse_script)

W, H, FPS = 1080, 1920, 30
S = 2                       # 카메라 이동용 2배 캔버스 (떨림 방지)
T = 0.45                    # 전환 효과 길이(초)
SAFE_TOP, SAFE_BOTTOM = int(H * 0.14), int(H * 0.65)   # 글자를 둘 수 있는 세로 범위
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
DEFAULT_FONT_FAMILY = "Noto Sans CJK KR"
TRANSITIONS = ["smoothup", "circleopen", "slideleft", "fade", "zoomin", "smoothleft",
               "dissolve", "wipeup", "radial", "coverleft"]
AUTO_MOVES = ["줌인", "아래로", "포커스위", "줌아웃", "포커스가운데", "위로", "포커스아래", "오른쪽"]


# ---------------------------------------------------------------- 공통 도구
def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"명령 실패: {' '.join(cmd[:8])} ...\n{r.stderr[-3000:]}")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def color(hex_str: str) -> str:
    return "0x" + hex_str.strip().lstrip("#")


def ass_color(hex_str: str, alpha: int = 0) -> str:
    """'#RRGGBB' → ASS 색상 '&HAABBGGRR' (alpha 0=불투명, 255=투명)."""
    h = hex_str.strip().lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def wrap(text: str, width: int) -> list[str]:
    """'|' 는 직접 줄바꿈. 쉼표가 있으면 쉼표 뒤에서 먼저 끊고, 나머지는 띄어쓰기 기준 자동 줄바꿈."""
    out: list[str] = []
    for part in str(text).split("|"):
        part = part.strip()
        pieces = [p.strip() for p in re.split(r"(?<=,)\s+", part)] if "," in part else [part]
        if any(len(p) > width for p in pieces):
            pieces = [part]
        for p in pieces:
            out += textwrap.wrap(p, width=width, break_long_words=False)
    return [w for w in out if w]


def font_family(font_path: str) -> str:
    if font_path == DEFAULT_FONT:
        return DEFAULT_FONT_FAMILY
    r = subprocess.run(["fc-scan", "--format", "%{family[0]}\n", font_path], capture_output=True, text=True)
    first = r.stdout.strip().splitlines()
    return first[0] if first else DEFAULT_FONT_FAMILY


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS)]   # 장면 중간 파일 공통 인코딩


def frames(dur: float) -> int:
    return max(2, round(dur * FPS))


def sweep_x(dur: float) -> str:
    """빛 반사 띠가 장면 길이의 80% 동안 왼쪽→오른쪽으로 지나가는 x 위치 식."""
    return f"-560+({W}+1120)*min(1,t/{max(0.8, dur * 0.8):.2f})"


# ---------------------------------------------------------------- 대본 → 장면
@dataclass
class Scene:
    kind: str                       # "poster" | "text"
    text: str                       # 나레이션(=자막) 문장
    image: int = 0
    move: str = "줌인"
    focus: tuple[float, float, float] | None = None
    panel: tuple[float, float, float, float] | None = None
    voice: Path | None = None
    voice_dur: float = 0.0
    dur: float = 0.0


TAG = re.compile(r"^\[(.+?)\]\s*(.*)$")


def plan_scenes(lines: list[str], n_images: int) -> list[Scene]:
    scenes: list[Scene] = []
    poster_count = 0
    for i, raw in enumerate(lines):
        m = TAG.match(raw)
        if m:
            tokens, text = m.group(1).split(), m.group(2).strip()
            head = tokens[0]
            if head.startswith("문구"):
                scenes.append(Scene("text", text))
                continue
            img = int(head[3:]) - 1 if head.startswith("포스터") and head[3:].isdigit() else poster_count
            img = max(0, min(n_images - 1, img))
            move = tokens[1] if len(tokens) > 1 else AUTO_MOVES[poster_count % len(AUTO_MOVES)]
            focus = panel = None
            if move == "확대" and len(tokens) >= 5:
                focus = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
            if move == "패널" and len(tokens) >= 6:
                panel = tuple(float(v) for v in tokens[2:6])
            scenes.append(Scene("poster", text, image=img % n_images, move=move, focus=focus, panel=panel))
            poster_count += 1
            continue
        # 자동 배치: 짝수 줄은 포스터, 홀수 줄은 문구 장면. 마지막 줄(행동유도)은 문구 장면
        if i % 2 == 1 or (i == len(lines) - 1 and len(lines) > 1):
            scenes.append(Scene("text", raw))
        else:
            scenes.append(Scene("poster", raw, image=poster_count % n_images,
                                move=AUTO_MOVES[poster_count % len(AUTO_MOVES)]))
            poster_count += 1
    return scenes


# ---------------------------------------------------------------- 음성
async def tts_line(text: str, voice: str, rate: str, out: Path) -> None:
    import edge_tts  # 지연 import: --dry-tts 때는 필요 없음
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


def make_voices(scenes: list[Scene], brand: dict, tmp: Path, dry: bool) -> None:
    for i, sc in enumerate(scenes):
        wav = tmp / f"voice{i:02d}.wav"
        if dry:
            d = max(1.4, len(sc.text) * 0.17)
            run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{d:.2f}", wav.as_posix()])
        else:
            raw = tmp / f"voice{i:02d}.mp3"
            asyncio.run(tts_line(sc.text.replace("|", " "), brand.get("음성", "ko-KR-SunHiNeural"),
                                 brand.get("말하기속도", "+0%"), raw))
            run(["ffmpeg", "-y", "-i", raw.as_posix(), "-ar", "48000", "-ac", "1", wav.as_posix()])
        sc.voice, sc.voice_dur = wav, duration(wav)
        sc.dur = sc.voice_dur + T + 0.35


# ---------------------------------------------------------------- 그림 재료
def make_canvas(img: Path, tmp: Path, idx: int) -> tuple[Path, tuple[int, int, int, int]]:
    """포스터를 2배 크기 9:16 캔버스에 배치(가로 꽉 채움, 남는 곳은 흐린 배경).
    반환: (캔버스 경로, 캔버스 안 포스터 위치 x, y, w, h)"""
    from PIL import Image, ImageFilter, ImageEnhance
    cw, ch = W * S, H * S
    src = Image.open(img).convert("RGB")
    bg = src.copy()
    r = max(cw / bg.width, ch / bg.height)
    bg = bg.resize((int(bg.width * r) + 1, int(bg.height * r) + 1))
    bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2, (bg.width - cw) // 2 + cw, (bg.height - ch) // 2 + ch))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(60))).enhance(0.78)
    r = min(cw / src.width, ch / src.height)
    fg = src.resize((int(src.width * r), int(src.height * r)), Image.LANCZOS)
    x, y = (cw - fg.width) // 2, (ch - fg.height) // 2
    bg.paste(fg, (x, y))
    out = tmp / f"canvas{idx}.png"
    bg.save(out)
    return out, (x, y, fg.width, fg.height)


def make_shine(tmp: Path) -> Path:
    """포스터 위를 지나가는 대각선 빛 반사 띠."""
    from PIL import Image, ImageDraw, ImageFilter
    band = Image.new("RGBA", (520, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.polygon([(180, 0), (340, 0), (220, H), (60, H)], fill=(255, 250, 240, 70))
    band = band.filter(ImageFilter.GaussianBlur(40))
    out = tmp / "shine.png"
    band.save(out)
    return out


def make_tint(brand: dict, tmp: Path) -> Path:
    """문구 장면용: 브랜드색 반투명 + 가장자리 어둡게."""
    from PIL import Image, ImageDraw, ImageFilter
    h = brand.get("메인색", "#523E2E").lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.new("RGBA", (W, H), rgb + (215,))
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse((-300, -200, W + 300, H + 200), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(220))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 120))
    dark.putalpha(Image.eval(vig, lambda v: int((255 - v) * 0.55)))
    im = Image.alpha_composite(im, dark)
    out = tmp / "tint.png"
    im.save(out)
    return out


# ---------------------------------------------------------------- 장면 렌더
def ease(n_frames: int) -> str:
    p = f"min(1,on/{max(1, n_frames - 1)})"
    return f"({p}*{p}*(3-2*{p}))"


def camera(sc: Scene, box: tuple[int, int, int, int], n: int) -> tuple[str, str, str]:
    """장면의 카메라 움직임 → zoompan 의 z, x, y 식."""
    cw, ch = W * S, H * S
    bx, by, bw, bh = box
    cy_top = (by + bh * 0.28) / ch
    cy_mid = (by + bh * 0.52) / ch
    cy_bot = (by + bh * 0.80) / ch
    mv = sc.move
    if sc.focus:                                   # [포스터 확대 x y 배율]
        fx, fy, fz = sc.focus
        tx, ty = (bx + bw * fx) / cw, (by + bh * fy) / ch
        z0, z1, cx0, cx1, cy0, cy1 = 1.0, max(1.05, fz), 0.5, tx, 0.5, ty
    else:
        z0, z1, cx0, cx1, cy0, cy1 = {
            "줌인": (1.0, 1.14, .5, .5, .5, .5),
            "줌아웃": (1.16, 1.0, .5, .5, .5, .5),
            "아래로": (1.32, 1.32, .5, .5, cy_top, cy_bot),
            "위로": (1.32, 1.32, .5, .5, cy_bot, cy_top),
            "오른쪽": (1.3, 1.3, .42, .58, .5, .5),
            "왼쪽": (1.3, 1.3, .58, .42, .5, .5),
            "포커스위": (1.0, 1.45, .5, .5, .5, cy_top),
            "포커스가운데": (1.0, 1.4, .5, .5, .5, cy_mid),
            "포커스아래": (1.0, 1.4, .5, .5, .5, cy_bot),
        }.get(mv, (1.0, 1.14, .5, .5, .5, .5))
    e = ease(n)
    z = f"{z0}+({z1 - z0})*{e}"
    x = f"max(0,min(iw-iw/zoom,({cx0}+({cx1 - cx0})*{e})*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,({cy0}+({cy1 - cy0})*{e})*ih-ih/zoom/2))"
    return z, x, y


def render_poster(sc: Scene, idx: int, canvas: tuple[Path, tuple], shine: Path, tmp: Path) -> Path:
    out = tmp / f"scene{idx:02d}.mp4"
    n = frames(sc.dur)
    z, x, y = camera(sc, canvas[1], n)
    filt = (f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},"
            f"vignette=PI/6,setsar=1[p];"
            f"[1:v]format=rgba[s];[p][s]overlay=x='{sweep_x(sc.dur)}':y=0:shortest=1,"
            f"format=yuv420p,settb=AVTB[v]")
    run(["ffmpeg", "-y", "-i", canvas[0].as_posix(), "-loop", "1", "-i", shine.as_posix(),
         "-filter_complex", filt, "-map", "[v]", "-frames:v", str(n), *X264, out.name], cwd=tmp)
    return out


def make_panel(img: Path, region: tuple[float, float, float, float], tmp: Path, idx: int) -> Path:
    """포스터의 한 부분을 잘라 그림자를 넣은 카드 이미지로 (부분 강조용)."""
    from PIL import Image, ImageDraw, ImageFilter
    src = Image.open(img).convert("RGB")
    x0, y0, x1, y1 = region
    crop = src.crop((int(src.width * x0), int(src.height * y0), int(src.width * x1), int(src.height * y1)))
    r = min(960 / crop.width, 860 / crop.height)
    crop = crop.resize((int(crop.width * r), int(crop.height * r)), Image.LANCZOS)
    pad = 60
    card = Image.new("RGBA", (crop.width + pad * 2, crop.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("L", card.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 14, pad + crop.width, pad + crop.height + 14), 28, fill=150)
    card.putalpha(shadow.filter(ImageFilter.GaussianBlur(24)))
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, crop.width - 1, crop.height - 1), 24, fill=255)
    card.paste(crop, (pad, pad), mask)
    out = tmp / f"panel{idx:02d}.png"
    card.save(out)
    return out


def render_panel(sc: Scene, idx: int, canvas: tuple[Path, tuple], img: Path, shine: Path, tmp: Path) -> Path:
    """흐리고 어두워진 포스터 위로 강조할 부분이 카드처럼 떠오름."""
    out = tmp / f"scene{idx:02d}.mp4"
    n = frames(sc.dur)
    panel = make_panel(img, sc.panel, tmp, idx)
    from PIL import Image
    ph = Image.open(panel).height
    cy = (SAFE_TOP + SAFE_BOTTOM) // 2 - 40
    p = f"min(1,max(0,(t-{T * 0.4:.2f})/0.5))"
    y = f"{cy - ph // 2}+70*(1-{p})*(1-{p})"
    filt = (f"[0:v]zoompan=z='1.0+0.06*on/{n}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={n}:s={W}x{H}:fps={FPS},"
            f"boxblur=8:1,eq=brightness=-0.18:saturation=0.85,setsar=1[b];"
            f"[1:v]format=rgba,fade=t=in:st={T * 0.4:.2f}:d=0.4:alpha=1[pn];"
            f"[b][pn]overlay=x=(W-w)/2:y='{y}':eval=frame:shortest=1[c];"
            f"[2:v]format=rgba[s];[c][s]overlay=x='{sweep_x(sc.dur)}':y=0:shortest=1,"
            f"format=yuv420p,settb=AVTB[v]")
    run(["ffmpeg", "-y", "-i", canvas[0].as_posix(), "-loop", "1", "-i", panel.as_posix(),
         "-loop", "1", "-i", shine.as_posix(), "-filter_complex", filt, "-map", "[v]",
         "-frames:v", str(n), *X264, out.name], cwd=tmp)
    return out


def rise(delay: float, base_y: int, dist: int = 46, dur: float = 0.45) -> tuple[str, str]:
    """글자가 아래에서 떠오르며 나타나는 효과 → (alpha, y) 식."""
    p = f"min(1,max(0,(t-{delay:.2f})/{dur}))"
    return f"{p}", f"{base_y}+{dist}*(1-{p})*(1-{p})"


def text_layers(tmp: Path, name: str, font: str, items: list[dict]) -> list[str]:
    """items: [{text, size, y, color, wrap, delay}] → drawtext 필터 목록 (줄마다 가운데 정렬·순차 등장)."""
    vf = []
    for i, it in enumerate(items):
        if not it.get("text"):
            continue
        for j, line in enumerate(wrap(it["text"], it.get("wrap", 12))):
            tf = tmp / f"{name}_{i}_{j}.txt"
            tf.write_text(line, encoding="utf-8")
            a, y = rise(it.get("delay", 0) + j * 0.12, it["y"] + j * int(it["size"] * 1.38))
            vf.append(f"drawtext=fontfile='{font}':textfile={tf.name}:fontsize={it['size']}"
                      f":fontcolor={it['color']}:alpha='{a}':x=(w-text_w)/2:y='{y}'"
                      f":shadowcolor=black@0.25:shadowx=0:shadowy=3")
    return vf


def block_height(text: str, size: int, wrap_w: int) -> int:
    return len(wrap(text, wrap_w)) * int(size * 1.38)


def render_card(name: str, dur: float, canvas: Path, tint: Path, layers: list[str],
                accent: tuple[int, str] | None, tmp: Path, logo: Path | None = None) -> Path:
    """흐린 포스터 + 브랜드색 배경 위에 움직이는 글자 (오프닝·문구 장면·엔딩 공용)."""
    out = tmp / f"{name}.mp4"
    n = frames(dur)
    zoom = f"1.0+0.05*on/{n}"
    chain = [f"[0:v]scale={W}:{H},boxblur=24:2,zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
             f":d={n}:s={W}x{H}:fps={FPS}[b]", "[1:v]format=rgba[t]", "[b][t]overlay=0:0:shortest=1[c]"]
    last = "c"
    vf = list(layers)
    if accent:
        ay, acol = accent
        vf.insert(0, f"drawbox=x=(iw-w)/2:y={ay}:w='160*min(1,max(0,(t-0.25)/0.5))':h=6:color={acol}:t=fill")
    chain.append(f"[{last}]{','.join(vf)}[d]")
    last = "d"
    inputs = ["-i", canvas.as_posix(), "-loop", "1", "-i", tint.as_posix()]
    if logo:
        inputs += ["-loop", "1", "-i", logo.as_posix()]
        chain.append(f"[2:v]scale=300:-1,format=rgba,fade=t=in:st=0:d=0.5:alpha=1[lg];"
                     f"[{last}][lg]overlay=(W-w)/2:{SAFE_TOP + 40}:shortest=1[e]")
        last = "e"
    chain.append(f"[{last}]format=yuv420p,settb=AVTB[v]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", "[v]",
         "-frames:v", str(n), *X264, out.name], cwd=tmp)
    return out


# ---------------------------------------------------------------- 자막
def make_ass(cues: list[tuple[float, float, str]], family: str, text_hex: str, box_hex: str) -> str:
    """포스터 장면 자막: 안전영역 하단(화면 65% 지점 위), 브랜드색 반투명 박스, 부드럽게 나타남."""
    margin_v = H - SAFE_BOTTOM + 40
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{family},66,{ass_color(text_hex)},{ass_color(text_hex)},"
        f"{ass_color(box_hex, 0x30)},{ass_color(box_hex, 0x30)},"
        f"-1,0,0,0,100,100,0,0,3,20,0,2,90,90,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    body = "".join(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{{\\fad(180,120)}}{t}\n"
                   for s, e, t in cues)
    return head + body


# ---------------------------------------------------------------- 전체 제작
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
    logo = logo if logo and logo.exists() else None
    bgm = COMMON_DESIGN_DIR / brand["배경음악파일"] if brand.get("배경음악파일") else None
    fg = color(brand.get("글자색", "#FFFFFF"))
    sub = color(brand.get("보조글자색", "#D9DEE6"))
    acc = color(brand.get("강조색", "#F2C14E"))
    transitions = brand.get("전환효과") or TRANSITIONS

    out_dir = OUTPUT_DIR / week
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scenes = plan_scenes(lines, len(images))
        make_voices(scenes, brand, tmp, dry)
        canvases = [make_canvas(img, tmp, i) for i, img in enumerate(images)]
        shine, tint = make_shine(tmp), make_tint(brand, tmp)
        top = SAFE_TOP + (260 if logo else 120)

        # 오프닝: 병원명 → 시술명이 차례로 떠오름 → 강조선
        intro_d = float(brand.get("오프닝길이", 2.4))
        title = brief.get("시술명", "")
        ty = 760
        intro = render_card("intro", intro_d, canvases[0][0], tint, text_layers(tmp, "intro", font, [
            {"text": brand.get("병원명", ""), "size": 50, "y": ty - 110, "color": sub, "wrap": 20, "delay": 0.05},
            {"text": title, "size": 100, "y": ty, "color": fg, "wrap": 9, "delay": 0.25},
        ]), (ty + block_height(title, 100, 9) + 30, acc), tmp, logo)

        clips = [intro]
        for i, sc in enumerate(scenes):
            if sc.kind == "poster" and sc.panel:
                clips.append(render_panel(sc, i, canvases[sc.image], images[sc.image], shine, tmp))
            elif sc.kind == "poster":
                clips.append(render_poster(sc, i, canvases[sc.image], shine, tmp))
            else:
                h = block_height(sc.text, 92, 10)
                y0 = max(top + 80, (SAFE_TOP + SAFE_BOTTOM) // 2 - h // 2)
                clips.append(render_card(f"scene{i:02d}", sc.dur, canvases[sc.image][0], tint,
                                         text_layers(tmp, f"s{i}", font, [
                                             {"text": brand.get("병원명", ""), "size": 40, "y": y0 - 110,
                                              "color": sub, "wrap": 20, "delay": 0.15},
                                             {"text": sc.text, "size": 92, "y": y0, "color": fg,
                                              "wrap": 10, "delay": T * 0.6},
                                         ]), (y0 + h + 34, acc), tmp))

        # 엔딩: 병원명·연락처·주소·고지문·심의번호 (모두 안전영역 안)
        outro_d = float(brand.get("엔딩길이", 3.5))
        review = f"의료광고심의 {brief['심의번호']}" if brief.get("심의번호") else ""
        oy = top + 200
        name_h = block_height(brand.get("병원명", ""), 96, 10)
        outro = render_card("outro", outro_d, canvases[0][0], tint, text_layers(tmp, "outro", font, [
            {"text": brand.get("병원명", ""), "size": 96, "y": oy, "color": fg, "wrap": 10, "delay": 0.1},
            {"text": brand.get("연락처", ""), "size": 64, "y": oy + name_h + 90, "color": fg, "wrap": 20, "delay": 0.35},
            {"text": brand.get("주소", ""), "size": 42, "y": oy + name_h + 190, "color": sub, "wrap": 24, "delay": 0.5},
            {"text": brand.get("고지문", ""), "size": 34, "y": SAFE_BOTTOM - 210, "color": sub, "wrap": 28, "delay": 0.7},
            {"text": review, "size": 30, "y": SAFE_BOTTOM - 70, "color": sub, "wrap": 30, "delay": 0.8},
        ]), (oy + name_h + 40, acc), tmp, logo)
        clips.append(outro)

        # 장면 시작 시각 계산 (전환 효과만큼 겹침)
        durs = [duration(c) for c in clips]
        starts = [0.0]
        for d in durs[:-1]:
            starts.append(starts[-1] + d - T)
        total = starts[-1] + durs[-1]

        # 전환 효과로 이어 붙이기
        inputs: list[str] = []
        for c in clips:
            inputs += ["-i", c.name]
        chain, prev = [], "0:v"
        for k in range(1, len(clips)):
            tr = transitions[(k - 1) % len(transitions)]
            outl = f"x{k}"
            chain.append(f"[{prev}][{k}:v]xfade=transition={tr}:duration={T}:offset={starts[k]:.3f}[{outl}]")
            prev = outl
        run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{prev}]",
             *X264, "-pix_fmt", "yuv420p", "joined.mp4"], cwd=tmp)

        # 음성 배치 + 자막 (포스터 장면만 자막, 문구 장면은 화면 글자가 곧 자막)
        cues_all, cues_sub = [], []
        for k, sc in enumerate(scenes, start=1):
            s = starts[k] + T * 0.6
            plain = sc.text.replace("|", " ")
            cues_all.append((s, s + sc.voice_dur, plain))
            if sc.kind == "poster":
                cues_sub.append((s, s + sc.voice_dur, plain))
        srt = "".join(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n" for i, (s, e, t) in enumerate(cues_all, 1))
        (out_dir / "reel.srt").write_text(srt, encoding="utf-8")
        fonts_dir = tmp / "fonts"
        fonts_dir.mkdir()
        shutil.copy(font, fonts_dir)
        (tmp / "subs.ass").write_text(make_ass(cues_sub, font_family(font), brand.get("글자색", "#FFFFFF"),
                                               brand.get("자막배경색", "#000000")), encoding="utf-8")

        cmd = ["ffmpeg", "-y", "-i", "joined.mp4"]
        amix = []
        for i, sc in enumerate(scenes):
            cmd += ["-i", sc.voice.name]
            ms = int(cues_all[i][0] * 1000)
            amix.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
        labels = "".join(f"[a{i}]" for i in range(len(scenes)))
        afilt = ";".join(amix) + f";{labels}amix=inputs={len(scenes)}:normalize=0,apad[voice]"
        amap = "[voice]"
        if bgm and bgm.exists():
            cmd += ["-stream_loop", "-1", "-i", bgm.as_posix()]
            vol = float(brand.get("배경음악볼륨", 0.12))
            afilt += (f";[{len(scenes) + 1}:a]volume={vol},afade=t=in:d=0.8,"
                      f"afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5[bg];"
                      f"[voice][bg]amix=inputs=2:duration=first:normalize=0[am]")
            amap = "[am]"
        final = out_dir / "reel.mp4"
        cmd += ["-filter_complex", f"[0:v]ass=subs.ass:fontsdir=fonts[v];{afilt}",
                "-map", "[v]", "-map", amap, "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-r", str(FPS), "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", final.as_posix()]
        run(cmd, cwd=tmp)

        # 표지: 오프닝 제목이 다 나타난 장면
        run(["ffmpeg", "-y", "-ss", f"{min(intro_d - 0.3, 1.6):.2f}", "-i", final.as_posix(), "-frames:v", "1",
             "-q:v", "2", (out_dir / "cover.jpg").as_posix()])
        plan = [{"순서": k + 1, "시작(초)": round(starts[k + 1], 2), "종류": "포스터" if sc.kind == "poster" else "문구",
                 "움직임": ("패널" if sc.panel else sc.move) if sc.kind == "poster" else "-",
                 "이미지": images[sc.image].name if sc.kind == "poster" else "-", "문장": sc.text}
                for k, sc in enumerate(scenes)]
        (out_dir / "scenes.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[완료] {final.relative_to(OUTPUT_DIR.parent)}  길이 {duration(final):.1f}초")
    return final


# ---------------------------------------------------------------- 재제작 판단
def fingerprint(week: str) -> str:
    """대본·포스터·공통설정이 바뀌었는지 판단하는 지문.
    (제작 코드는 넣지 않음: 코드만 고쳐서 이미 발행한 지난 주차 영상이 다시 만들어지는 일을 막기 위해)"""
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
        # 품질 점검 (규격·길이·음성 + 장면 시트)
        subprocess.run([sys.executable, str(Path(__file__).with_name("qa_reel.py")), week], check=False)


if __name__ == "__main__":
    main()
