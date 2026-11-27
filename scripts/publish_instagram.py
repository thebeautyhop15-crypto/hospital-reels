"""완성된 릴스를 인스타그램에 발행 (Instagram API with Instagram Login — 페이스북 페이지 불필요).

조건 (모두 맞아야 발행)
  · 02_script/<주차>/script.md 상태가 '승인'
  · 04_output/<주차>/reel.mp4 가 있고 품질 점검(qa.txt)에 '확인필요'가 없음
  · 04_output/<주차>/published_instagram.json 이 없음 (중복 발행 방지)

영상 주소: GitHub Pages(https://<계정>.github.io/<저장소>/04_output/<주차>/reel.mp4)
  인스타그램은 공개 주소의 영상을 직접 가져가므로 저장소 Pages 가 켜져 있어야 합니다.

환경변수
  IG_ACCESS_TOKEN  (필수, GitHub Secrets) 인스타그램 사용자 장기 토큰(60일)
  IG_API_VERSION   (선택, 기본 v26.0)
  PAGES_BASE       (선택, 기본 https://<계정>.github.io/<저장소>)  — Actions 가 자동으로 넣음

사용
  python scripts/publish_instagram.py               # 이번 주(한국 시간)
  python scripts/publish_instagram.py 2026-W49
  python scripts/publish_instagram.py 2026-W49 --check   # 발행하지 않고 조건·주소·토큰만 점검
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common import APPROVED, KST, OUTPUT_DIR, SCRIPT_DIR, current_week, load_brand, load_brief, parse_script

HOST = "https://graph.instagram.com"


def api(method: str, path: str, params: dict) -> dict:
    ver = os.environ.get("IG_API_VERSION") or "v26.0"
    url = f"{HOST}/{ver}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(f"{url}?{data.decode()}", method="GET")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit(f"인스타그램 API 오류 ({e.code}) {path}\n{body}")


def head_ok(url: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200, r.headers.get("Content-Type", "")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def caption(brand: dict, brief: dict) -> str:
    """기획서의 '캡션'이 있으면 그대로, 없으면 시술명·소개·행동유도·고지문·해시태그로 구성."""
    if brief.get("캡션"):
        return str(brief["캡션"]).strip()
    parts = [f"[{brand.get('병원명', '')}] {brief.get('시술명', '')}".strip(),
             brief.get("한줄소개", ""), "", brief.get("행동유도", "")]
    if brand.get("연락처") and brand["연락처"] not in str(brief.get("행동유도", "")):   # 전화번호 중복 방지
        parts.append(f"☎ {brand['연락처']}")
    parts += ["", brand.get("고지문", "").replace("|", " ")]
    if brief.get("심의번호"):
        parts.append(f"의료광고심의 {brief['심의번호']}")
    tags = brief.get("해시태그") or brand.get("기본해시태그") or []
    if tags:
        parts += ["", " ".join(t if t.startswith("#") else f"#{t}" for t in tags)]
    return "\n".join(p for p in parts if p is not None).strip()[:2200]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("week", nargs="?", default=None)
    ap.add_argument("--check", action="store_true", help="발행하지 않고 점검만")
    a = ap.parse_args()
    week = a.week or current_week()
    out = OUTPUT_DIR / week
    stamp = out / "published_instagram.json"

    # 1) 발행 조건
    meta, _ = parse_script(SCRIPT_DIR / week / "script.md") if (SCRIPT_DIR / week / "script.md").exists() else ({}, [])
    problems = []
    if str(meta.get("상태", "")).strip() != APPROVED:
        problems.append(f"대본이 '{APPROVED}' 상태가 아님 (현재: {meta.get('상태')})")
    if not (out / "reel.mp4").exists():
        problems.append("완성 영상(reel.mp4)이 없음")
    qa = (out / "qa.txt").read_text(encoding="utf-8") if (out / "qa.txt").exists() else ""
    if "확인필요" in qa:
        problems.append("품질 점검에 '확인필요' 항목이 있음")
    if stamp.exists():
        print(f"[건너뜀] {week}: 이미 인스타그램에 발행됨 → {stamp.read_text(encoding='utf-8')}")
        return

    # 2) 영상 공개 주소
    base = (os.environ.get("PAGES_BASE") or "").rstrip("/")
    video_url = f"{base}/04_output/{week}/reel.mp4"
    cover_url = f"{base}/04_output/{week}/cover.jpg"
    ok, ctype = head_ok(video_url)
    if not ok:
        problems.append(f"영상 공개 주소에 접근할 수 없음 (GitHub Pages 확인): {video_url} — {ctype}")

    # 3) 토큰 확인
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("IG_ACCESS_TOKEN 이 없습니다. GitHub Secrets 에 등록하세요.")
    me = api("GET", "me", {"fields": "user_id,username", "access_token": token})
    ig_id = me.get("user_id") or me.get("id")
    print(f"인스타그램 계정: @{me.get('username')} ({ig_id})")

    text = caption(load_brand(), load_brief(week))
    print(f"영상 주소: {video_url} ({ctype})\n--- 캡션 ---\n{text}\n-----------")
    if problems:
        print("[발행 불가]\n - " + "\n - ".join(problems))
        sys.exit(0 if a.check else 1)
    if a.check:
        print("[점검 완료] 발행 조건 충족. --check 이므로 발행하지 않음.")
        return

    # 4) 컨테이너 생성 → 처리 완료 대기 → 발행
    c = api("POST", f"{ig_id}/media", {"media_type": "REELS", "video_url": video_url, "cover_url": cover_url,
                                        "caption": text, "share_to_feed": "true", "access_token": token})
    cid = c["id"]
    print(f"컨테이너 생성: {cid}")
    for i in range(15):                      # 1분 간격, 최대 15분
        time.sleep(60 if i else 30)
        st = api("GET", cid, {"fields": "status_code,status", "access_token": token})
        print(f"  처리 상태: {st.get('status_code')} {st.get('status', '')}")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            sys.exit(f"영상 처리 실패: {st}")
    else:
        sys.exit("영상 처리가 15분 안에 끝나지 않았습니다. 다음 실행에서 다시 시도합니다.")
    pub = api("POST", f"{ig_id}/media_publish", {"creation_id": cid, "access_token": token})
    mid = pub["id"]
    info = api("GET", mid, {"fields": "permalink,media_product_type,timestamp", "access_token": token})
    record = {"media_id": mid, "permalink": info.get("permalink"), "type": info.get("media_product_type"),
              "published_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "account": me.get("username")}
    stamp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[발행 완료] {record['permalink']}")


if __name__ == "__main__":
    main()
