"""Gemini API (무료 등급) — 글·사진을 함께 보내고 JSON 으로 답을 받음.

환경변수: GEMINI_API_KEY (필수), GEMINI_MODEL (선택, 기본 gemini-2.5-flash)
무료 등급은 입력이 Google 제품 개선에 쓰일 수 있으므로 환자 개인정보는 보내지 마세요.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def ask_json(prompt: str, images: list[Path] | None = None, retries: int = 3) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    parts = [{"text": prompt}]
    for p in images or []:
        parts.append({"inline_data": {"mime_type": MIME.get(p.suffix.lower(), "image/png"),
                                      "data": base64.b64encode(p.read_bytes()).decode()}})
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"}}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last = ""
    for i in range(retries):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            return json.loads(text)
        except urllib.error.HTTPError as e:
            last = f"{e.code} {e.read().decode(errors='replace')[:400]}"
            if e.code not in (429, 500, 503):
                break
        except (KeyError, json.JSONDecodeError) as e:
            last = f"응답 해석 실패: {e}"
        time.sleep(8 * (i + 1))
    raise RuntimeError(f"Gemini 요청 실패: {last}")
