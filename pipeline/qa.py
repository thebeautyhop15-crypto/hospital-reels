"""영상 품질 점검 → 05_video/<주차>/qa.txt, qa_sheet.jpg

  규격 1080x1920·30fps·H.264/AAC, 길이 4~90초, 음성 유무, 음량(-14 LUFS 근처), 파일 크기,
  장면 시트(1초 간격 화면 + 안전영역 선: 위 14%, 아래 35%, 좌우 6%)
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw

from common import H, SAFE, W


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def loudness(path: Path) -> tuple[float, float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    r2 = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                        capture_output=True, text=True)
    mx = re.findall(r"max_volume: (-?[\d.]+) dB", r2.stderr)
    return (float(i[-1]) if i else -99.0), (float(mx[-1]) if mx else -99.0)


def sheet(path: Path, dur: float, out: Path) -> None:
    times = [round(t + 0.5, 2) for t in range(int(dur))][:32]
    tw, th, cols = 216, 384, 8
    rows = (len(times) + cols - 1) // cols
    board = Image.new("RGB", (cols * tw, rows * th), "white")
    k = tw / W
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            f = Path(td) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(path),
                            "-frames:v", "1", "-vf", f"scale={tw}:{th}", str(f)], check=True)
            im = Image.open(f).convert("RGB")
            d = ImageDraw.Draw(im)
            d.rectangle((SAFE["side"] * k, SAFE["top"] * k, tw - SAFE["side"] * k, SAFE["bottom"] * k),
                        outline=(255, 0, 80), width=1)
            d.text((5, 3), f"{t:.1f}s", fill=(255, 0, 80))
            board.paste(im, ((i % cols) * tw, (i // cols) * th))
    board.save(out, quality=86)


def check(path: Path, real_voice: bool = True) -> bool:
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    fps = float(Fraction(v["r_frame_rate"]))
    lufs, peak = loudness(path) if a else (-99.0, -99.0)
    size = path.stat().st_size
    rows = [
        ("해상도 1080x1920 (9:16)", v["width"] == W and v["height"] == H, f'{v["width"]}x{v["height"]}'),
        ("프레임 30fps", abs(fps - 30) < 0.5, f"{fps:.2f}"),
        ("영상 코덱 H.264", v["codec_name"] == "h264", v["codec_name"]),
        ("음성 코덱 AAC", bool(a) and a["codec_name"] == "aac", a["codec_name"] if a else "없음"),
        ("길이 4~90초", 4 <= dur <= 90, f"{dur:.1f}초"),
        ("실제 음성 사용", real_voice, "Edge TTS" if real_voice else "음성 서비스 연결 안 됨 — 예상 길이 무음"),
        ("음량 -16~-12 LUFS", -16 <= lufs <= -12 or not real_voice, f"{lufs:.1f} LUFS, 최고 {peak:.1f}dB"),
        ("파일 크기 100MB 이하", size < 100 * 1024 * 1024, f"{size / 1e6:.1f}MB"),
    ]
    text = "\n".join(f"{'통과' if ok else '확인필요'} | {n} | {val}" for n, ok, val in rows)
    (path.parent / "qa.txt").write_text(text + "\n", encoding="utf-8")
    sheet(path, dur, path.parent / "qa_sheet.jpg")
    print(text)
    return all(ok for _, ok, _ in rows)
