"""완성 영상 품질 점검 → 04_output/<주차>/qa.txt, qa_sheet.jpg

점검 항목
  · 규격: 1080x1920(9:16), 30fps, H.264 + AAC  — 인스타·페이스북 릴스 권장 규격
  · 길이: 4~90초 (페이스북 페이지 릴스 API 허용 범위)
  · 음성: 소리가 실제로 들어 있는지 (무음 여부)
  · 장면 시트: 1.5초 간격 화면 모음 + 안전영역(위 14%, 아래 35%, 좌우 6%) 표시선

사용: python scripts/qa_reel.py 2026-W49
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from common import OUTPUT_DIR, current_week

W, H = 1080, 1920


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def mean_volume(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split(":")[1].replace("dB", "").strip())
    return -999.0


def sheet(path: Path, dur: float, out: Path) -> None:
    from PIL import Image, ImageDraw
    times = [round(t * 1.5, 2) for t in range(int(dur / 1.5) + 1)][:16]
    tw, th = 270, 480
    cols = 8
    rows = (len(times) + cols - 1) // cols
    board = Image.new("RGB", (cols * tw, rows * th), "white")
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            f = Path(td) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(path),
                            "-frames:v", "1", "-vf", f"scale={tw}:{th}", str(f)], check=True)
            im = Image.open(f).convert("RGB")
            d = ImageDraw.Draw(im)
            d.rectangle((int(tw * 0.06), int(th * 0.14), int(tw * 0.94), int(th * 0.65)), outline=(255, 0, 80), width=2)
            d.text((6, 4), f"{t:.1f}s", fill=(255, 0, 80))
            board.paste(im, ((i % cols) * tw, (i // cols) * th))
    board.save(out, quality=88)


def main() -> None:
    week = sys.argv[1] if len(sys.argv) > 1 else current_week()
    path = OUTPUT_DIR / week / "reel.mp4"
    if not path.exists():
        sys.exit(f"영상이 없습니다: {path}")
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    fps = float(Fraction(v["r_frame_rate"]))  # 예: "30/1"
    vol = mean_volume(path) if a else -999.0
    checks = [
        ("해상도 1080x1920", v["width"] == W and v["height"] == H, f'{v["width"]}x{v["height"]}'),
        ("프레임 30fps", abs(fps - 30) < 0.5, f"{fps:.2f}"),
        ("영상 코덱 H.264", v["codec_name"] == "h264", v["codec_name"]),
        ("음성 코덱 AAC", bool(a) and a["codec_name"] == "aac", a["codec_name"] if a else "없음"),
        ("길이 4~90초", 4 <= dur <= 90, f"{dur:.1f}초"),
        ("음성 들어 있음", vol > -50, f"평균 {vol:.1f}dB" + (" (무음 미리보기)" if vol <= -50 else "")),
        ("파일 크기 100MB 이하", path.stat().st_size < 100 * 1024 * 1024, f"{path.stat().st_size / 1e6:.1f}MB"),
    ]
    lines = [f"{'통과' if ok else '확인필요'} | {name} | {val}" for name, ok, val in checks]
    report = "\n".join(lines)
    (OUTPUT_DIR / week / "qa.txt").write_text(report + "\n", encoding="utf-8")
    sheet(path, dur, OUTPUT_DIR / week / "qa_sheet.jpg")
    print(report)


if __name__ == "__main__":
    main()
