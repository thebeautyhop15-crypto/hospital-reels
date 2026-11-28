"""프레임 합성기 — 장면 하나를 1080x1920 30fps 영상으로 그립니다.

장면 종류(시나리오의 '종류')
  후킹   : 포스터 일부를 크게 당겨 보여주며 후킹 문구가 단어별로 튀어나옴 (+ 흔들림·강조 박스)
  포스터 : 카메라 무브(줌·이동·포커스·초점 당김) + 빛 반사 + 단어 강조 자막
  패널   : 포스터의 한 영역을 카드로 잘라 띄움(튀어오름·떠 있음·반짝임) + 자막
  문구   : 큰 글씨 키네틱 타이포(떠오름·팝·타자·마스크) + 강조 단어 밑줄
  문의   : 연락처 크게 + 맥박 효과 + 버튼·화살표
  엔딩   : 병원명·영문명·연락처·주소·고지문이 차례로 등장
모든 장면 공통: 빛번짐, 빛 입자, 비네팅, 필름 질감 (config/style.yml 에서 조절)
"""
from __future__ import annotations

import math
import random
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from common import FPS, H, SAFE, W

S = 2  # 카메라용 2배 캔버스


# ---------------------------------------------------------------- 기본 도구
def rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def smooth(p):
    p = clamp(p)
    return p * p * (3 - 2 * p)


def out_cubic(p):
    p = clamp(p)
    return 1 - (1 - p) ** 3


def out_back(p, s=1.9):
    p = clamp(p) - 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def prog(t, start, length):
    return clamp((t - start) / max(1e-6, length))


@lru_cache(maxsize=64)
def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=2048)
def word_image(text: str, font_path: str, size: int, color: tuple, shadow: bool = True,
               box: tuple | None = None) -> Image.Image:
    """단어 하나를 그림자 포함 RGBA 이미지로 (자주 쓰므로 캐시)."""
    f = get_font(font_path, size)
    x0, y0, x1, y1 = f.getbbox(text)
    pad = int(size * 0.32)
    w, h = x1 - x0 + pad * 2, int(size * 1.32) + pad
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if box:                                          # 강조 박스(형광펜)
        bx = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(bx).rounded_rectangle((pad * 0.45, pad * 0.55, w - pad * 0.45, h - pad * 0.25),
                                             radius=int(size * 0.16), fill=box)
        im.alpha_composite(bx)
    if shadow:
        sh = Image.new("L", (w, h), 0)
        ImageDraw.Draw(sh).text((pad - x0, pad - y0 + size * 0.06), text, font=f, fill=150)
        sh = sh.filter(ImageFilter.GaussianBlur(size * 0.07))
        black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        black.putalpha(sh)
        im.alpha_composite(black)
    ImageDraw.Draw(im).text((pad - x0, pad - y0), text, font=f, fill=color + (255,))
    return im


def paste(frame: Image.Image, im: Image.Image, cx: float, cy: float, alpha: float = 1.0, scale: float = 1.0):
    """가운데 기준 붙이기 (투명도·크기 변화 포함)."""
    if alpha <= 0.01 or scale <= 0.01:
        return
    if abs(scale - 1) > 0.01:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if alpha < 0.99:
        a = im.getchannel("A").point(lambda v: int(v * alpha))
        im = im.copy()
        im.putalpha(a)
    x, y = int(cx - im.width / 2), int(cy - im.height / 2)
    if x >= W or y >= H or x + im.width <= 0 or y + im.height <= 0:
        return
    sx, sy = max(0, -x), max(0, -y)
    if sx or sy or x + im.width > W or y + im.height > H:
        im = im.crop((sx, sy, min(im.width, W - x), min(im.height, H - y)))
        x, y = x + sx, y + sy
    frame.alpha_composite(im, (x, y))


# ---------------------------------------------------------------- 글자 배치
@dataclass
class Word:
    text: str
    key: bool = False           # 강조 단어
    start: float = 0.0          # 말하기 시작(장면 기준 초)
    end: float = 0.0
    x: float = 0.0
    y: float = 0.0
    line: int = 0


def split_words(text: str) -> list[Word]:
    """'**강조**' 와 '|'(줄바꿈)을 해석해 단어 목록으로."""
    out = []
    for part_i, part in enumerate(text.split("|")):
        for tok in part.split():
            key = "**" in tok
            out.append(Word(tok.replace("**", ""), key))
        if part_i < len(text.split("|")) - 1 and out:
            out[-1].line = -1                       # 줄바꿈 표시
    return out


def layout(words: list[Word], font_path: str, size: int, max_w: int, cy: float, gap: float = 1.32) -> int:
    """단어를 줄 단위로 가운데 정렬. 반환: 줄 수."""
    f = get_font(font_path, size)
    space = max(f.getlength(" "), size * 0.3)
    lines, cur, cur_w = [], [], 0.0
    for wd in words:
        ww = f.getlength(wd.text)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append(wd)
        cur_w += (space if len(cur) > 1 else 0) + ww
        if wd.line == -1:
            lines.append(cur)
            cur, cur_w = [], 0.0
    if cur:
        lines.append(cur)
    lh = size * gap
    top = cy - lh * (len(lines) - 1) / 2
    for li, ln in enumerate(lines):
        total = sum(f.getlength(w.text) for w in ln) + space * (len(ln) - 1)
        x = W / 2 - total / 2
        for wd in ln:
            ww = f.getlength(wd.text)
            wd.x, wd.y, wd.line = x + ww / 2, top + li * lh, li
            x += ww + space
    return len(lines)


# ---------------------------------------------------------------- 장면 준비물
@dataclass
class Ctx:
    brand: dict
    style: dict
    fonts: dict                          # 역할 → (경로, 크기)
    images: list[Path]
    seed: int = 1
    preview: bool = False                # True 면 '검수용 미리보기' 표시
    canvases: list = field(default_factory=list)   # (2배 캔버스, 포스터 박스)
    blurs: list = field(default_factory=list)      # 흐린 배경
    sprites: dict = field(default_factory=dict)

    def color(self, name: str, default="#FFFFFF") -> tuple:
        v = self.brand.get(name, default)
        return rgb(v if str(v).startswith("#") else self.brand.get(v, default))

    def prepare(self):
        cw, ch = W * S, H * S
        for p in self.images:
            src = Image.open(p).convert("RGB")
            r = max(cw / src.width, ch / src.height)
            bg = src.resize((int(src.width * r) + 2, int(src.height * r) + 2), Image.BILINEAR)
            bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2, (bg.width - cw) // 2 + cw,
                          (bg.height - ch) // 2 + ch)).filter(ImageFilter.GaussianBlur(70))
            bg = ImageEnhance.Brightness(bg).enhance(0.7)
            r = min(cw / src.width, ch / src.height)
            fg = src.resize((int(src.width * r), int(src.height * r)), Image.LANCZOS)
            x, y = (cw - fg.width) // 2, (ch - fg.height) // 2
            bg.paste(fg, (x, y))
            self.canvases.append((bg, (x, y, fg.width, fg.height), src))
            b = src.copy()
            r = max(W / b.width, H / b.height)
            b = b.resize((int(b.width * r) + 2, int(b.height * r) + 2), Image.BILINEAR)
            b = b.crop(((b.width - W) // 2, (b.height - H) // 2, (b.width - W) // 2 + W, (b.height - H) // 2 + H))
            b = b.filter(ImageFilter.GaussianBlur(38))
            self.blurs.append(b.convert("RGBA"))
        eff = self.style.get("효과", {})
        self.sprites["tint"] = self._tint()
        self.sprites["vignette"] = self._vignette(float(eff.get("비네팅", 0.35)))
        self.sprites["shine"] = self._shine()
        self.sprites["leak"] = self._leak()
        self.sprites["dots"] = [self._dot(r) for r in (6, 10, 16)]
        rng = np.random.default_rng(self.seed)
        g = float(eff.get("필름질감", 0.035)) * 255
        self.sprites["grain"] = [rng.normal(0, g, (H, W, 1)).astype(np.int16) for _ in range(6)] if g > 0 else []
        prng = random.Random(self.seed)
        self.sprites["particles"] = [(prng.random() * W, prng.random() * H, prng.randint(0, 2),
                                      12 + prng.random() * 30, prng.random() * 6.28, 0.25 + prng.random() * 0.5)
                                     for _ in range(26)]

    # 준비물 그리기 -------------------------------------------------
    def _tint(self):
        im = Image.new("RGBA", (W, H), self.color("메인색") + (205,))
        grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * 0.45))
        dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        dark.putalpha(grad)
        im.alpha_composite(dark)
        return im

    def _vignette(self, k):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).ellipse((-W * 0.35, -H * 0.22, W * 1.35, H * 1.22), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(260)).point(lambda v: int((255 - v) * k))
        v = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        v.putalpha(m)
        return v

    def _shine(self):
        band = Image.new("RGBA", (560, H), (0, 0, 0, 0))
        ImageDraw.Draw(band).polygon([(200, 0), (360, 0), (220, H), (60, H)], fill=(255, 248, 235, 60))
        return band.filter(ImageFilter.GaussianBlur(42))

    def _leak(self):
        size = int(W * 1.2)
        m = Image.radial_gradient("L").resize((size, size)).point(lambda v: int((255 - v) * 0.42))
        c = Image.new("RGBA", (size, size), (255, 196, 128, 255))
        c.putalpha(m)
        return c

    def _dot(self, r):
        s = r * 6
        m = Image.radial_gradient("L").resize((s, s)).point(lambda v: max(0, 255 - v * 1.25))
        d = Image.new("RGBA", (s, s), (255, 240, 215, 255))
        d.putalpha(m.point(lambda v: int(v * 0.55)))
        return d


# ---------------------------------------------------------------- 공통 효과
def overlays(frame: Image.Image, t: float, ctx: Ctx, particles=True, leak=True):
    eff = ctx.style.get("효과", {})
    if leak and eff.get("빛번짐", True):
        lk = ctx.sprites["leak"]
        x = int(-lk.width * 0.45 + math.sin(t * 0.35) * 80)
        y = int(-lk.height * 0.38 + math.cos(t * 0.27) * 60)
        sub = lk.crop((max(0, -x), max(0, -y), lk.width, lk.height))
        frame.alpha_composite(sub, (max(0, x), max(0, y)))
    if particles and eff.get("입자", True):
        for (x0, y0, k, sp, ph, al) in ctx.sprites["particles"]:
            y = (y0 - t * sp) % H
            x = x0 + math.sin(t * 0.8 + ph) * 18
            dot = ctx.sprites["dots"][k]
            a = al * (0.6 + 0.4 * math.sin(t * 2 + ph))
            paste(frame, dot, x, y, alpha=a)
    frame.alpha_composite(ctx.sprites["vignette"])


def finish(frame: Image.Image, i: int, ctx: Ctx) -> bytes:
    arr = np.asarray(frame.convert("RGB"), dtype=np.int16)
    if ctx.sprites["grain"]:
        arr = arr + ctx.sprites["grain"][i % len(ctx.sprites["grain"])]
    return np.clip(arr, 0, 255).astype(np.uint8).tobytes()


def shine(frame: Image.Image, t: float, dur: float, ctx: Ctx):
    """포스터 위를 대각선 빛이 한 번 지나감."""
    if not ctx.style.get("효과", {}).get("빛반사", True):
        return
    p = prog(t, 0.25, max(0.9, dur * 0.7))
    if 0 < p < 1:
        paste(frame, ctx.sprites["shine"], -280 + (W + 560) * p, H / 2)


# ---------------------------------------------------------------- 카메라
MOVES = {   # (시작배율, 끝배율, 시작x, 끝x, 시작y, 끝y)  — 포스터 기준 좌표(0~1)
    "줌인": (1.0, 1.14, .5, .5, .5, .5), "줌아웃": (1.18, 1.0, .5, .5, .5, .5),
    "아래로": (1.35, 1.35, .5, .5, .3, .72), "위로": (1.35, 1.35, .5, .5, .72, .3),
    "오른쪽": (1.32, 1.32, .4, .6, .5, .5), "왼쪽": (1.32, 1.32, .6, .4, .5, .5),
    "대각선": (1.12, 1.3, .38, .62, .35, .62), "펀치줌": (1.45, 1.22, .5, .5, .5, .5),
    "전체공개": (2.0, 1.0, .5, .5, .3, .5),
}


def camera(ctx: Ctx, img: int, t: float, dur: float, move: str, focus=None) -> Image.Image:
    canvas, (bx, by, bw, bh), _ = ctx.canvases[img]
    cw, ch = canvas.size
    if focus:                                             # [x0,y0,x1,y1] 영역으로 서서히 다가감
        fx, fy = (focus[0] + focus[2]) / 2, (focus[1] + focus[3]) / 2
        fz = min(2.4, max(1.15, 0.92 / max(focus[2] - focus[0], (focus[3] - focus[1]) * bh / bw * W / H)))
        if move == "펀치줌":
            z0, z1, x0, x1, y0, y1 = fz * 0.82, fz * 1.06, fx, fx, fy, fy
        elif move == "전체공개":
            z0, z1, x0, x1, y0, y1 = fz, 1.0, fx, .5, fy, .5
        else:
            z0, z1, x0, x1, y0, y1 = 1.0, fz, .5, fx, .5, fy
    else:
        z0, z1, x0, x1, y0, y1 = MOVES.get(move, MOVES["줌인"])
    p = out_cubic(t / 0.6) * 0.35 + smooth(t / dur) * 0.65 if move == "펀치줌" else smooth(t / dur)
    z = z0 + (z1 - z0) * p
    px, py = x0 + (x1 - x0) * p, y0 + (y1 - y0) * p
    cx, cy = bx + bw * px, by + bh * py
    vw, vh = cw / z, ch / z
    lx0, lx1 = (bx, bx + bw - vw) if vw <= bw else (0, cw - vw)      # 확대 시 포스터 밖 여백이 보이지 않게
    ly0, ly1 = (by, by + bh - vh) if vh <= bh else (0, ch - vh)
    left = clamp(cx - vw / 2, lx0, lx1)
    top = clamp(cy - vh / 2, ly0, ly1)
    if move == "펀치줌" and t < 0.35:                    # 등장 순간 흔들림
        a = (0.35 - t) * 40
        left = clamp(left + math.sin(t * 90) * a, 0, cw - vw)
        top = clamp(top + math.cos(t * 70) * a, 0, ch - vh)
    fr = canvas.resize((W, H), Image.BILINEAR, box=(left, top, left + vw, top + vh), reducing_gap=1.5)
    return fr.convert("RGBA")


# ---------------------------------------------------------------- 자막 (단어 강조)
def subtitle(frame: Image.Image, t: float, words: list[Word], ctx: Ctx, appear: float = 0.15):
    if not words:
        return
    path, size = ctx.fonts["자막"]
    cy = H * float(ctx.style.get("자막", {}).get("세로위치", 0.6))
    if not hasattr(words[0], "_laid"):
        n = layout(words, path, size, W - SAFE["side"] * 2 - 90, cy)
        words[0]._laid = n
    n = words[0]._laid
    a = out_cubic(prog(t, appear, 0.25))
    if a <= 0:
        return
    f = get_font(path, size)
    lh = size * 1.32
    # 줄마다 배경 박스
    box_col = ctx.color("메인색")
    for li in range(n):
        ln = [w for w in words if w.line == li]
        x0 = min(w.x - f.getlength(w.text) / 2 for w in ln) - 30
        x1 = max(w.x + f.getlength(w.text) / 2 for w in ln) + 30
        y = ln[0].y
        bx = Image.new("RGBA", (int(x1 - x0), int(lh + 18)), (0, 0, 0, 0))
        ImageDraw.Draw(bx).rounded_rectangle((0, 0, bx.width - 1, bx.height - 1), radius=22,
                                             fill=box_col + (int(205 * a),))
        frame.alpha_composite(bx, (int(x0), int(y - lh / 2 + 4 + (1 - a) * 20)))
    base, hi = ctx.color("글자색"), ctx.color("강조색")
    mode = ctx.style.get("자막", {}).get("방식", "단어강조")
    for w in words:
        speaking = w.start <= t < w.end
        done = t >= w.end
        if mode == "팝업":
            col, al, sc = (hi if w.key else base), a, 1.0
        else:
            col = hi if (speaking or (done and w.key)) else base
            al = a * (1.0 if (speaking or done) else 0.62)
            sc = 1.0 + 0.05 * math.sin(math.pi * clamp((t - w.start) / max(0.12, w.end - w.start))) if speaking else 1.0
        im = word_image(w.text, path, size, col, True)
        paste(frame, im, w.x, w.y + (1 - a) * 20 + size * 0.08, alpha=al, scale=sc)


# ---------------------------------------------------------------- 키네틱 타이포
def kinetic(frame: Image.Image, t: float, words: list[Word], role: str, ctx: Ctx, cy: float,
            effect: str = "떠오름", delay: float = 0.2, stagger: float = 0.09, max_w: int | None = None,
            color=None, key_color=None, key_box=False, size: int | None = None):
    if not words:
        return
    path, base_size = ctx.fonts[role]
    size = size or base_size
    if not getattr(words[0], "_laid", None):
        words[0]._laid = layout(words, path, size, max_w or (W - SAFE["side"] * 2 - 60), cy)
    color = color or ctx.color("글자색")
    key_color = key_color or ctx.color("강조색")
    f = get_font(path, size)
    for k, w in enumerate(words):
        start = delay + k * stagger
        col = key_color if (w.key and not key_box) else color
        boxc = (key_color + (255,)) if (w.key and key_box) else None
        if w.key and key_box:
            col = ctx.color("메인색")
        im = word_image(w.text, path, size, col, not key_box or not w.key, boxc)
        if effect == "팝":
            p = prog(t, start, 0.38)
            paste(frame, im, w.x, w.y, alpha=clamp(p * 3), scale=max(0.01, out_back(p, 2.4)))
        elif effect == "타자":
            p = prog(t, start, 0.05 * max(1, len(w.text)))
            if p > 0:
                n = max(1, int(round(len(w.text) * p)))
                part = word_image(w.text[:n], path, size, col, True, boxc)
                paste(frame, part, w.x - (im.width - part.width) / 2, w.y)
        elif effect == "마스크":
            p = smooth(prog(t, delay + w.line * 0.22, 0.55))
            if p > 0:
                cut = im.crop((0, 0, max(1, int(im.width * p)), im.height))
                paste(frame, cut, w.x - (im.width - cut.width) / 2, w.y)
        else:   # 떠오름
            p = prog(t, start, 0.45)
            paste(frame, im, w.x, w.y + 46 * (1 - out_cubic(p)) ** 2, alpha=out_cubic(p))
        if w.key and not key_box:                           # 강조 단어 밑줄이 그어짐
            p = smooth(prog(t, start + 0.35, 0.4))
            if p > 0:
                ww = f.getlength(w.text) * p
                bar = Image.new("RGBA", (max(1, int(ww)), 7), key_color + (230,))
                frame.alpha_composite(bar, (int(w.x - f.getlength(w.text) / 2), int(w.y + size * 0.62)))


def small_label(frame, t, text, ctx, y, delay=0.05, role="보조", color=None, spacing=False):
    if not text:
        return
    path, size = ctx.fonts[role]
    if spacing:
        text = " ".join(text)
    im = word_image(text, path, size, color or ctx.color("보조글자색"), True)
    p = out_cubic(prog(t, delay, 0.5))
    paste(frame, im, W / 2, y + 18 * (1 - p), alpha=p)


def accent_line(frame, t, y, ctx, delay=0.3, width=170):
    p = smooth(prog(t, delay, 0.5))
    if p > 0:
        w = max(1, int(width * p))
        frame.alpha_composite(Image.new("RGBA", (w, 6), ctx.color("포인트색") + (255,)), (int(W / 2 - w / 2), int(y)))


# ---------------------------------------------------------------- 장면별 그리기
def bg_blur(ctx: Ctx, img: int, t: float, dur: float) -> Image.Image:
    b = ctx.blurs[img]
    z = 1.0 + 0.06 * smooth(t / dur)
    vw, vh = W / z, H / z
    fr = b.resize((W, H), Image.BILINEAR, box=((W - vw) / 2, (H - vh) / 2, (W + vw) / 2, (H + vh) / 2))
    fr.alpha_composite(ctx.sprites["tint"])
    return fr


def draw_hook(ctx, sc, t, dur):
    fr = camera(ctx, sc.get("이미지", 0), t, dur, "펀치줌", sc.get("영역"))
    if "dim" not in ctx.sprites:                          # 글자가 잘 보이도록, 아래로 갈수록 더 어둡게
        g = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(90 + v * 0.55))
        dim = Image.new("RGBA", (W, H), (18, 11, 7, 255))
        dim.putalpha(g)
        ctx.sprites["dim"] = dim
    fr.alpha_composite(ctx.sprites["dim"])
    small_label(fr, t, ctx.brand.get("병원명", ""), ctx, SAFE["top"] + 90, role="보조")
    kinetic(fr, t, sc["_words"], "후킹", ctx, H * 0.42, effect="팝", delay=0.12, stagger=0.11, key_box=True)
    overlays(fr, t, ctx, particles=False)
    return fr


def draw_poster(ctx, sc, t, dur):
    fr = camera(ctx, sc.get("이미지", 0), t, dur, sc.get("움직임", "줌인"), sc.get("영역"))
    if sc.get("움직임") == "초점":                       # 흐림 → 선명
        r = 14 * (1 - out_cubic(prog(t, 0, 0.7)))
        if r > 0.5:
            fr = fr.filter(ImageFilter.GaussianBlur(r))
    shine(fr, t, dur, ctx)
    overlays(fr, t, ctx)
    subtitle(fr, t, sc["_words"], ctx)
    return fr


def draw_panel(ctx, sc, t, dur):
    img = sc.get("이미지", 0)
    fr = bg_blur(ctx, img, t, dur)
    key = "_panel"
    if key not in sc:
        _, _, src = ctx.canvases[img]
        x0, y0, x1, y1 = sc["영역"]
        crop = src.crop((int(src.width * x0), int(src.height * y0), int(src.width * x1), int(src.height * y1)))
        r = min(940 / crop.width, 760 / crop.height)
        crop = crop.resize((int(crop.width * r), int(crop.height * r)), Image.LANCZOS)
        pad = 70
        card = Image.new("RGBA", (crop.width + pad * 2, crop.height + pad * 2), (0, 0, 0, 0))
        shadow = Image.new("L", card.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 18, pad + crop.width, pad + crop.height + 18), 30, fill=170)
        card.putalpha(shadow.filter(ImageFilter.GaussianBlur(28)))
        mask = Image.new("L", crop.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, crop.width - 1, crop.height - 1), 26, fill=255)
        card.paste(crop, (pad, pad), mask)
        ImageDraw.Draw(card).rounded_rectangle((pad - 3, pad - 3, pad + crop.width + 2, pad + crop.height + 2), 28,
                                               outline=ctx.color("포인트색") + (255,), width=4)
        sc[key] = card
    card = sc[key]
    cy = H * 0.40
    p = prog(t, 0.18, 0.5)
    float_y = math.sin(max(0, t - 0.7) * 2.2) * 6
    paste(fr, card, W / 2, cy + 70 * (1 - out_cubic(p)) + float_y, alpha=clamp(p * 2.5), scale=0.86 + 0.14 * out_back(p))
    if sc.get("라벨"):                                     # 카드 위 제목 (명조, 왼쪽부터 드러남)
        if "_label" not in sc:
            sc["_label"] = [Word(t) for t in sc["라벨"].split()]
        ty = max(SAFE["top"] + 70, cy - card.height / 2 - 20)
        small_label(fr, t, ctx.brand.get("영문명", ""), ctx, ty - 75, delay=0.05, spacing=True)
        kinetic(fr, t, sc["_label"], "제목", ctx, ty, effect="마스크", delay=0.1,
                size=int(ctx.fonts["제목"][1] * 0.62))
    if 0.55 < t:                                            # 카드 위 반짝임
        q = prog(t, 0.6, 0.8)
        if 0 < q < 1:
            band = ctx.sprites["shine"].resize((260, card.height - 120))
            paste(fr, band, W / 2 - card.width / 2 + card.width * q, cy + float_y, alpha=0.9)
    overlays(fr, t, ctx)
    subtitle(fr, t, sc["_words"], ctx, appear=0.3)
    return fr


def draw_text(ctx, sc, t, dur):
    fr = bg_blur(ctx, sc.get("이미지", 0), t, dur)
    path, size = ctx.fonts["본문"]
    words = sc["_words"]
    effect = sc.get("글자효과", "떠오름")
    cy = (SAFE["top"] + SAFE["bottom"]) / 2
    small_label(fr, t, ctx.brand.get("영문명", ""), ctx, cy - 260, delay=0.1, spacing=True)
    kinetic(fr, t, words, "본문", ctx, cy, effect=effect, delay=0.3)
    n = getattr(words[0], "_laid", 1)
    accent_line(fr, t, cy + size * 1.32 * n / 2 + 40, ctx, delay=0.55)
    overlays(fr, t, ctx)
    return fr


def draw_cta(ctx, sc, t, dur):
    fr = bg_blur(ctx, sc.get("이미지", 0), t, dur)
    cy = (SAFE["top"] + SAFE["bottom"]) / 2 - 60
    kinetic(fr, t, sc["_words"], "본문", ctx, cy - 150, effect="떠오름", delay=0.25, size=int(ctx.fonts["본문"][1] * 0.8))
    phone = ctx.brand.get("연락처", "")
    if phone:
        path, size = ctx.fonts["강조"]
        im = word_image(phone, path, int(size * 1.05), ctx.color("강조색"), True)
        p = out_back(prog(t, 0.55, 0.45))
        pulse = 1 + 0.035 * math.sin(max(0, t - 1.0) * 5.5)
        paste(fr, im, W / 2, cy + 40, alpha=clamp(p * 2), scale=max(0.01, p * pulse))
    act = ctx.brand.get("행동유도", "")
    if act:                                                 # 버튼 + 위로 가리키는 화살표
        path, size = ctx.fonts["보조"]
        txt = word_image(act, path, int(size * 1.1), ctx.color("메인색"), False)
        bw, bh = txt.width + 60, txt.height + 26
        btn = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(btn).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=bh // 2, fill=ctx.color("강조색") + (255,))
        btn.alpha_composite(txt, ((bw - txt.width) // 2, (bh - txt.height) // 2 + 4))
        q = out_cubic(prog(t, 0.95, 0.45))
        by = cy + 230
        paste(fr, btn, W / 2, by + 30 * (1 - q), alpha=q)
        if q > 0.5:
            bob = math.sin(t * 6) * 10
            arrow = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
            ImageDraw.Draw(arrow).polygon([(30, 4), (56, 40), (38, 40), (38, 58), (22, 58), (22, 40), (4, 40)],
                                          fill=ctx.color("글자색") + (235,))
            paste(fr, arrow, W / 2, by - bh / 2 - 60 + bob, alpha=q)
    overlays(fr, t, ctx)
    return fr


def draw_outro(ctx, sc, t, dur):
    fr = bg_blur(ctx, 0, t, dur)
    top = SAFE["top"] + 150
    path, size = ctx.fonts["제목"]
    name = [Word(ctx.brand.get("병원명", ""))]
    kinetic(fr, t, name, "제목", ctx, top + 80, effect="마스크", delay=0.1)
    small_label(fr, t, ctx.brand.get("영문명", ""), ctx, top + 175, delay=0.4, spacing=True)
    accent_line(fr, t, top + 225, ctx, delay=0.5)
    pp, ps = ctx.fonts["강조"]
    if ctx.brand.get("연락처"):
        im = word_image(ctx.brand["연락처"], pp, int(ps * 0.78), ctx.color("글자색"), True)
        p = out_cubic(prog(t, 0.6, 0.45))
        paste(fr, im, W / 2, top + 330 + 20 * (1 - p), alpha=p)
    small_label(fr, t, ctx.brand.get("주소", ""), ctx, top + 420, delay=0.75)
    disc = ctx.brand.get("고지문", "")
    lines = [l for l in disc.split("|") if l.strip()]
    for i, ln in enumerate(lines):
        bp, bs = ctx.fonts["보조"]
        im = word_image(ln.strip(), bp, int(bs * 0.82), ctx.color("보조글자색"), True)
        p = out_cubic(prog(t, 0.95 + i * 0.1, 0.4))
        paste(fr, im, W / 2, SAFE["bottom"] - 170 + i * 46, alpha=p * 0.95)
    if sc.get("심의번호"):
        small_label(fr, t, f"의료광고심의 {sc['심의번호']}", ctx, SAFE["bottom"] - 50, delay=1.2)
    overlays(fr, t, ctx)
    return fr


def preview_badge(frame, ctx):
    if "badge" not in ctx.sprites:
        path, size = ctx.fonts["보조"]
        ctx.sprites["badge"] = word_image("검수용 미리보기 · 발행 전", path, int(size * 0.8), (255, 255, 255), False,
                                          (200, 40, 60, 210))
    paste(frame, ctx.sprites["badge"], W / 2, SAFE["top"] - 70, alpha=0.9)


DRAW = {"후킹": draw_hook, "포스터": draw_poster, "패널": draw_panel, "문구": draw_text,
        "문의": draw_cta, "엔딩": draw_outro}


def render(ctx: Ctx, sc: dict, dur: float, out: Path) -> Path:
    """장면 하나를 그려 mp4 로 저장 (ffmpeg 에 프레임을 바로 넘김)."""
    n = max(2, round(dur * FPS))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    draw = DRAW[sc["종류"]]
    for i in range(n):
        fr = draw(ctx, sc, i / FPS, dur)
        if ctx.preview:
            preview_badge(fr, ctx)
        proc.stdin.write(finish(fr, i, ctx))
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"장면 렌더 실패: {sc.get('종류')}")
    return out
