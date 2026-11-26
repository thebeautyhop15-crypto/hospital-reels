"""공통 유틸: 경로, 설정 읽기, 대본 파싱."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = ROOT / "01_plan"
SCRIPT_DIR = ROOT / "02_script"
DESIGN_DIR = ROOT / "03_design"
COMMON_DESIGN_DIR = DESIGN_DIR / "_common"
OUTPUT_DIR = ROOT / "04_output"
KST = dt.timezone(dt.timedelta(hours=9))

APPROVED = "승인"


def current_week() -> str:
    """한국 시간 기준 ISO 주차 (예: 2026-W49)."""
    y, w, _ = dt.datetime.now(KST).isocalendar()
    return f"{y}-W{w:02d}"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_brand() -> dict:
    return load_yaml(ROOT / "config" / "brand.yml")


def load_brief(week: str) -> dict:
    return load_yaml(PLAN_DIR / week / "brief.yml")


_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_script(path: Path) -> tuple[dict, list[str]]:
    """script.md → (머리말 dict, 나레이션 줄 목록).

    나레이션은 '# 나레이션' 아래의 '- ' 로 시작하는 줄만 읽습니다.
    """
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    m = _FRONT.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        text = text[m.end():]
    lines: list[str] = []
    in_section = False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#"):
            heading = s.lstrip("#").strip()
            if heading.startswith("나레이션"):
                in_section = True
            elif heading.startswith("검수"):
                in_section = False
            continue  # 그 외 '#' 줄은 설명 주석으로 무시
        if in_section and s.startswith("- "):
            line = s[2:].strip()
            if line:
                lines.append(line)
    return meta, lines
