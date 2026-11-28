"""모든 단계가 함께 쓰는 경로·설정·기록(manifest) 도구.

단계와 폴더 (주차 폴더 이름으로 서로 연결됨)
  00_inbox/                     사진 넣는 곳 (누구나)
  01_plan/<주차>/               기획   : source/(원본 사진), analysis.json(사진 분석), brief.yml, manifest.yml
  02_script/<주차>/             대본   : script.md (후킹 + 나레이션, 승인 상태)
  03_scenario/<주차>/           시나리오: scenario.yml (장면별 연출·효과·폰트·전환)
  04_audio/<주차>/              음향   : voice_XX.mp3, timeline.json, mix.m4a
  05_video/<주차>/              영상   : reel.mp4, cover.jpg, qa.txt, qa_sheet.jpg
  06_publish/<주차>/            발행   : post.yml (발행 대기열·캡션·발행 결과)
manifest.yml 에 각 단계의 완료 시각·입력 지문이 기록되어, 앞 단계가 바뀌면 뒤 단계가 다시 만들어집니다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "00_inbox"
PLAN, SCRIPT, SCENARIO, AUDIO, VIDEO, PUBLISH = (ROOT / d for d in (
    "01_plan", "02_script", "03_scenario", "04_audio", "05_video", "06_publish"))
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
CONFIG = ROOT / "config"
KST = dt.timezone(dt.timedelta(hours=9))
APPROVED = "승인"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
W, H, FPS = 1080, 1920, 30
SAFE = {"top": int(H * 0.14), "bottom": int(H * 0.65), "side": int(W * 0.06)}   # 릴스 안전영역


# ---------------------------------------------------------------- 시간·주차
def now() -> dt.datetime:
    return dt.datetime.now(KST)


def week_of(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def current_week() -> str:
    return week_of(now().date())


def next_free_week() -> str:
    """기획 폴더가 아직 없는 가장 가까운 다음 주차."""
    d = now().date() + dt.timedelta(days=7)
    while (PLAN / week_of(d)).exists():
        d += dt.timedelta(days=7)
    return week_of(d)


# ---------------------------------------------------------------- 파일 입출력
def load_yaml(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or ({} if default is None else default)


def save_yaml(path: Path, data, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text((header + "\n" if header else "") + text, encoding="utf-8")


def load_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def brand() -> dict:
    return load_yaml(CONFIG / "brand.yml")


def style() -> dict:
    s = load_yaml(CONFIG / "style.yml")
    theme_name = brand().get("테마") or s.get("기본테마") or next(iter(s.get("테마", {})), "")
    s["현재테마"] = s.get("테마", {}).get(theme_name, {})
    s["현재테마이름"] = theme_name
    return s


def font(role: str) -> str:
    """역할(후킹·제목·본문·자막·강조·보조) → 폰트 파일 경로."""
    t = style()["현재테마"]
    name = (t.get(role) or t.get("본문") or {}).get("폰트", "Pretendard-Bold.otf")
    p = FONTS / name
    if not p.exists():
        p = ASSETS / "brand" / name
    return str(p)


def images(week: str) -> list[Path]:
    return sorted(p for p in (PLAN / week / "source").glob("*") if p.suffix.lower() in IMG_EXT)


# ---------------------------------------------------------------- 단계 기록(manifest)
STAGES = ["plan", "script", "scenario", "audio", "video", "publish"]


def manifest(week: str) -> dict:
    return load_yaml(PLAN / week / "manifest.yml", {"주차": week, "단계": {}})


def fingerprint(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        for f in sorted([p] if p.is_file() else (p.rglob("*") if p.exists() else [])):
            if f.is_file():
                h.update(f.name.encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


def mark(week: str, stage: str, inputs: str, **info) -> None:
    m = manifest(week)
    prev = m.get("단계", {}).get(stage, {})
    if prev.get("입력지문") == inputs and all(prev.get(k) == v for k, v in info.items()):
        return                                           # 같은 내용이면 기록하지 않음(불필요한 커밋 방지)
    m.setdefault("단계", {})[stage] = {"완료": now().isoformat(timespec="seconds"), "입력지문": inputs, **info}
    save_yaml(PLAN / week / "manifest.yml", m, "# 자동 기록: 각 단계 완료 시각과 입력 지문 (수정하지 마세요)")


def stage_done(week: str, stage: str, inputs: str) -> bool:
    return manifest(week).get("단계", {}).get(stage, {}).get("입력지문") == inputs


# ---------------------------------------------------------------- 대본 읽기
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_script(path: Path) -> dict:
    """script.md → {meta, hook, lines, caption, tags}.
    '## 후킹' 아래 '- ' 한 줄, '## 나레이션' 아래 '- ' 여러 줄, '## 캡션' 본문, '## 해시태그' (#태그 나열).
    **단어** 는 강조 단어, | 는 화면 줄바꿈. '#' 로 시작하는 줄과 '>' 줄은 설명·후보라 읽지 않음."""
    text = path.read_text(encoding="utf-8")
    meta = {}
    m = _FRONT.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        text = text[m.end():]
    names = {"후킹": "hook", "나레이션": "line", "캡션": "caption", "해시태그": "tags"}
    section, hook, lines, cap, tags = "", "", [], [], []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("## "):
            head = s[3:].strip()
            section = next((v for k, v in names.items() if head.startswith(k)), "")
            continue
        if s.startswith("#") and section != "tags" or s.startswith(">"):
            continue
        if section == "caption":
            cap.append(raw.rstrip())
        elif section == "tags":
            tags += [t.lstrip("#") for t in s.split() if t.startswith("#") and len(t) > 1 and " " not in t]
        elif s.startswith("- "):
            body = s[2:].strip()
            if section == "hook" and not hook:
                hook = body
            elif section == "line" and body:
                lines.append(body)
    return {"meta": meta, "hook": hook, "lines": lines, "caption": "\n".join(cap).strip(), "tags": tags}


def strip_marks(text: str) -> str:
    """강조 표시(**)와 줄바꿈 표시(|)를 뺀 순수 문장 — 음성·자막 파일용."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("|", " ").strip()


def keywords(text: str) -> list[str]:
    return re.findall(r"\*\*(.+?)\*\*", text)


# ---------------------------------------------------------------- 실행 도구
def run(cmd: list[str], cwd: Path | None = None, input_bytes: bytes | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, input=input_bytes)
    if r.returncode != 0:
        sys.exit(f"명령 실패: {' '.join(map(str, cmd[:8]))} ...\n{r.stderr.decode(errors='replace')[-3000:]}")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def log(msg: str) -> None:
    print(f"[{now():%H:%M:%S}] {msg}", flush=True)
