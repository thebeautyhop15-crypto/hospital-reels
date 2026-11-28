"""6단계 발행 — 승인된 확정 영상을 발행 대기열(06_publish/<주차>/post.yml)에 올리고, 인스타그램에 발행.

post.yml
  상태      : 대기 | 발행완료 | 보류        ('보류'로 바꾸면 발행하지 않음)
  예약일    : 비우면 다음 정기 발행 때 / 'YYYY-MM-DD' 면 그날 이후 첫 정기 발행 때
  캡션·해시태그 : 대본(02_script)의 '## 캡션', '## 해시태그' + 병원 정보·고지문으로 자동 작성 (여기서 고쳐도 됨)
  인스타그램 : 발행 결과(게시물 주소·시각) 자동 기록

발행 방식 (Meta 인증 방식에 따라 자동 선택)
  · IG_USER_ID 가 있으면  graph.facebook.com/{IG_USER_ID}  — 비즈니스 포트폴리오(시스템 사용자 토큰) 또는 페이스북 페이지 연결 방식
  · 없으면              graph.instagram.com/me           — 인스타그램 로그인 방식
영상 주소: PAGES_BASE/05_video/<주차>/reel.mp4 (GitHub Pages 가 켜져 있어야 인스타그램이 가져갈 수 있음)

환경변수: IG_ACCESS_TOKEN(필수), IG_USER_ID(선택), IG_API_VERSION(선택, 기본 v26.0), PAGES_BASE
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from common import (APPROVED, PLAN, PUBLISH, SCRIPT, VIDEO, brand, load_yaml, log, now, parse_script, save_yaml)

HEADER = "# 발행 대기열 — 설명은 pipeline/stage_publish.py 맨 위 참고"


def caption(week: str) -> tuple[str, list[str]]:
    b = brand()
    sc = parse_script(SCRIPT / week / "script.md")
    brief = load_yaml(PLAN / week / "brief.yml")
    parts = [sc["caption"] or f"[{b.get('병원명', '')}] {brief.get('시술명', '')}\n{brief.get('한줄소개', '')}", ""]
    act = b.get("행동유도", "")
    if act:
        parts.append(f"📍 {act}")
    if b.get("연락처") and b["연락처"] not in "".join(parts):
        parts.append(f"☎ {b['연락처']}")
    parts += ["", b.get("고지문", "").replace("|", " ")]
    if brief.get("심의번호"):
        parts.append(f"의료광고심의 {brief['심의번호']}")
    tags = list(dict.fromkeys((sc["tags"] or brief.get("해시태그") or []) + (b.get("기본해시태그") or [])))[:15]
    return "\n".join(parts).strip(), tags


def queue(week: str) -> None:
    """확정 영상이 있고 대본이 승인이면 post.yml 생성(이미 있으면 캡션 등 사람이 고친 내용 유지)."""
    meta = parse_script(SCRIPT / week / "script.md")["meta"]
    if str(meta.get("상태", "")).strip() != APPROVED or not (VIDEO / week / "reel.mp4").exists():
        return
    p = PUBLISH / week / "post.yml"
    if p.exists():
        return
    text, tags = caption(week)
    save_yaml(p, {"상태": "대기", "예약일": str(load_yaml(PLAN / week / "brief.yml").get("발행예정일", "") or ""),
                  "영상": f"05_video/{week}/reel.mp4", "표지": f"05_video/{week}/cover.jpg",
                  "캡션": text, "해시태그": tags, "인스타그램": {}}, HEADER)
    log(f"발행 대기열 등록 → 06_publish/{week}/post.yml")


# ---------------------------------------------------------------- 인스타그램 API
def api(method: str, host: str, path: str, params: dict) -> dict:
    ver = os.environ.get("IG_API_VERSION") or "v26.0"
    url = f"{host}/{ver}/{path}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{url}?{data.decode()}" if method == "GET" else url,
                                 data=None if method == "GET" else data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"인스타그램 API 오류 ({e.code}) {path}\n{e.read().decode(errors='replace')}")


def url_ok(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            return r.status == 200, r.headers.get("Content-Type", "")
    except Exception as e:                                   # noqa: BLE001
        return False, str(e)


def due() -> list[str]:
    today = now().date().isoformat()
    out = []
    for p in sorted(PUBLISH.glob("*/post.yml")):
        d = load_yaml(p)
        if d.get("상태") == "대기" and (not d.get("예약일") or str(d["예약일"]) <= today):
            out.append(p.parent.name)
    return out


def publish(week: str, check_only: bool = True) -> None:
    p = PUBLISH / week / "post.yml"
    post = load_yaml(p)
    if not post:
        sys.exit(f"{week}: 발행 대기열(post.yml)이 없습니다. 대본을 '승인'으로 바꾸면 만들어집니다.")
    if post.get("상태") != "대기":
        print(f"[건너뜀] {week}: 상태 '{post.get('상태')}'")
        return
    problems = []
    qa = (VIDEO / week / "qa.txt").read_text(encoding="utf-8") if (VIDEO / week / "qa.txt").exists() else "없음"
    if "확인필요" in qa or qa == "없음":
        problems.append("품질 점검(qa.txt)에 '확인필요' 항목이 있거나 점검 기록이 없음")
    base = (os.environ.get("PAGES_BASE") or "").rstrip("/")
    video_url, cover_url = f"{base}/{post['영상']}", f"{base}/{post['표지']}"
    ok, ctype = url_ok(video_url)
    if not ok or "video" not in ctype:
        problems.append(f"영상 공개 주소 확인 실패 (GitHub Pages 필요): {video_url} — {ctype}")
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("IG_ACCESS_TOKEN 이 없습니다. GitHub → Settings → Secrets 에 등록하세요.")
    ig = os.environ.get("IG_USER_ID")
    host = "https://graph.facebook.com" if ig else "https://graph.instagram.com"
    me = api("GET", host, ig or "me", {"fields": "username" if ig else "user_id,username", "access_token": token})
    ig = ig or me.get("user_id") or me.get("id")
    text = (post.get("캡션", "") + "\n\n" + " ".join(f"#{t}" for t in post.get("해시태그", []))).strip()[:2200]
    print(f"계정: @{me.get('username')} ({ig})\n영상: {video_url}\n--- 캡션 ---\n{text}\n------------")
    if problems:
        print("[발행 불가]\n - " + "\n - ".join(problems))
        sys.exit(0 if check_only else 1)
    if check_only:
        print("[점검 완료] 조건 충족 — 점검 모드라 발행하지 않음")
        return
    c = api("POST", host, f"{ig}/media", {"media_type": "REELS", "video_url": video_url, "cover_url": cover_url,
                                           "caption": text, "share_to_feed": "true", "access_token": token})
    for i in range(15):
        time.sleep(30 if i == 0 else 60)
        st = api("GET", host, c["id"], {"fields": "status_code,status", "access_token": token})
        print(f"  처리 상태: {st.get('status_code')}")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            sys.exit(f"영상 처리 실패: {st}")
    else:
        sys.exit("15분 안에 처리가 끝나지 않았습니다. 다음 실행에서 다시 시도합니다.")
    mid = api("POST", host, f"{ig}/media_publish", {"creation_id": c["id"], "access_token": token})["id"]
    info = api("GET", host, mid, {"fields": "permalink,timestamp", "access_token": token})
    post["상태"] = "발행완료"
    post["인스타그램"] = {"게시물": info.get("permalink"), "media_id": mid, "계정": me.get("username"),
                     "발행시각": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")}
    save_yaml(p, post, HEADER)
    print(f"[발행 완료] {info.get('permalink')}")
