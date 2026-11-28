"""4단계 음향 — 시나리오의 대사를 음성으로 만들고, 장면 시간표와 최종 믹스를 만듦 → 04_audio/<주차>/

  voice_XX.mp3   장면별 음성 (Edge TTS, 단어 시점 포함)
  timeline.json  장면별 시작·길이와 단어별 말하는 시점 (영상 단계가 자막 강조에 사용)
  bgm.wav        직접 합성한 배경음 (저작권 걱정 없음) — config/style.yml '배경음'에 파일명을 쓰면 assets/bgm/ 파일 사용
  mix.m4a        음성 + 배경음(음성 나올 때 자동으로 작아짐) + 효과음, 음량 -14 LUFS 로 맞춤

음성 서비스에 닿지 않으면(오프라인 등) 예상 길이의 무음으로 대신 만들고 timeline.json 에 '음성: 예상' 으로 표시합니다.
"""
from __future__ import annotations

import asyncio
import os

import synth
from common import (ASSETS, AUDIO, SCENARIO, brand, duration, fingerprint, log, mark, run, save_json, stage_done,
                    strip_marks, style, load_json, CONFIG)

LEAD = 0.32          # 장면 시작 후 말 시작까지
TAIL = 0.5           # 말 끝난 뒤 여유
MIN = {"후킹": 2.3, "문의": 3.4, "엔딩": 3.2}
TICK = 1e7           # Edge TTS 시간 단위(100ns)


async def _tts(text: str, path, voice: str, rate: str) -> list[dict]:
    import edge_tts
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    bounds = []
    with open(path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                bounds.append({"text": ch["text"], "start": ch["offset"] / TICK,
                               "end": (ch["offset"] + ch["duration"]) / TICK})
    return bounds


def speak(text: str, path, b: dict) -> tuple[float, list[dict], bool]:
    """→ (길이, 단어시점, 실제음성여부)"""
    try:
        bounds = asyncio.run(_tts(text, path, b.get("음성", "ko-KR-SunHiNeural"), b.get("말하기속도", "+0%")))
        if path.stat().st_size < 1000:
            raise RuntimeError("빈 음성")
        return duration(path), bounds, True
    except Exception as e:                                   # noqa: BLE001
        if os.environ.get("REQUIRE_TTS"):
            raise SystemExit(f"음성 생성 실패: {e}")
        est = 0.35 + len(text.replace(" ", "")) * 0.155
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{est:.2f}",
             "-c:a", "libmp3lame", str(path)])
        return est, [], False


def align(display: list[str], bounds: list[dict], total: float) -> list[tuple[float, float]]:
    """화면 단어(강조 표시 제거) ↔ 음성 단어 시점. 글자 위치 비율로 맞춤. 시점 정보가 없으면 글자 수 비례."""
    lens = [max(1, len(w)) for w in display]
    n = sum(lens)
    if not bounds:
        out, t = [], 0.05
        span = max(0.4, total - 0.15)
        for L in lens:
            d = span * L / n
            out.append((t, t + d))
            t += d
        return out
    blen = [max(1, len(b["text"].replace(" ", ""))) for b in bounds]
    bn = sum(blen)
    cum, marks = 0, []                                       # (글자비율 시작, 끝, 시작초, 끝초)
    for b, L in zip(bounds, blen):
        marks.append((cum / bn, (cum + L) / bn, b["start"], b["end"]))
        cum += L

    def at(frac: float) -> float:
        for f0, f1, s, e in marks:
            if frac <= f1 + 1e-9:
                return s + (e - s) * clamp((frac - f0) / max(1e-9, f1 - f0))
        return marks[-1][3]

    out, c = [], 0
    for L in lens:
        s, e = at(c / n), at((c + L) / n)
        out.append((s, max(e, s + 0.12)))
        c += L
    return out


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def display_words(text: str) -> list[str]:
    return [t.replace("**", "") for t in text.replace("|", " ").split()]


def build(week: str, scenes: list[dict]) -> dict:
    out = AUDIO / week
    out.mkdir(parents=True, exist_ok=True)
    st = style()
    b = brand()
    fp = fingerprint(SCENARIO / week / "scenario.yml", CONFIG / "brand.yml", CONFIG / "style.yml")
    tl_path = out / "timeline.json"
    if stage_done(week, "audio", fp) and tl_path.exists() and (out / "mix.m4a").exists():
        tl = load_json(tl_path)
        if tl.get("음성") == "실제" or not _tts_possible():
            return tl
    T = float(st.get("효과", {}).get("전환길이", 0.42))
    items, start, real = [], 0.0, True
    for i, sc in enumerate(scenes):
        text = sc.get("대사", "")
        item = {"종류": sc["종류"], "words": []}
        if text:
            v = out / f"voice_{i:02d}.mp3"
            vd, bounds, ok = speak(strip_marks(text), v, b)
            real &= ok
            lead = 0.18 if sc["종류"] == "후킹" else LEAD
            dur = max(MIN.get(sc["종류"], 2.6), lead + vd + TAIL)
            times = align(display_words(text), bounds, vd)
            item.update(voice=v.name, voice_at=lead, voice_len=round(vd, 3),
                        words=[{"text": w, "start": round(lead + s, 3), "end": round(lead + e, 3)}
                               for w, (s, e) in zip(display_words(text), times)])
        else:
            dur = MIN.get(sc["종류"], 2.6)
        item.update(start=round(start, 3), dur=round(dur, 3))
        items.append(item)
        start += dur - T
    total = round(start + T, 3)
    tl = {"주차": week, "전환길이": T, "길이": total, "음성": "실제" if real else "예상", "장면": items}
    mix(out, items, total, T, st, week)
    save_json(tl_path, tl)
    mark(week, "audio", fp, 길이=total, 음성=tl["음성"])
    log(f"음향 {total:.1f}초 (음성 {tl['음성']}) → {out.relative_to(AUDIO.parent)}")
    return tl


def _tts_possible() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def n_in(args: list) -> int:
    return args.count("-i")


def mix(out, items, total, T, st, week):
    snd = st.get("음향", {})
    bgm_opt = str(snd.get("배경음", "자동"))
    inputs, filt, voices = [], [], []
    for it in items:
        if it.get("voice"):
            k = n_in(inputs)
            inputs += ["-i", str(out / it["voice"])]
            ms = int((it["start"] + it["voice_at"]) * 1000)
            filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms},apad[v{k}]")
            voices.append(f"[v{k}]")
    if not voices:
        raise SystemExit("대사가 있는 장면이 없습니다.")
    filt.append(f"{''.join(voices)}amix=inputs={len(voices)}:normalize=0,atrim=0:{total},asplit=2[vo][sc]")
    bus = ["[vo]"]
    if bgm_opt != "없음":
        if bgm_opt == "자동":
            bgm = synth.pad(total + 1, out / "bgm.wav", seed=sum(map(ord, week)))
        else:
            bgm = ASSETS / "bgm" / bgm_opt
        k = n_in(inputs)
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        vol = float(snd.get("배경음크기", 0.22))
        filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total},volume={vol},"
                    f"afade=t=in:d=0.6,afade=t=out:st={max(0, total - 1.4):.2f}:d=1.4[bg0]")
        filt.append("[bg0][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[bg]")
        bus.append("[bg]")
    else:
        filt.append("[sc]anullsink")
    if snd.get("효과음", True):
        fx = []
        wh = synth.whoosh(out / "sfx_whoosh.wav")
        pp = synth.pop(out / "sfx_pop.wav")
        sh = synth.shimmer(out / "sfx_shimmer.wav")
        fx.append((pp, 0.1, 0.55))
        for it in items[1:]:
            fx.append((wh, max(0, it["start"] - 0.12), 0.5))
            if it["종류"] in ("패널", "엔딩"):
                fx.append((sh, it["start"] + 0.45, 0.35))
        for j, (f, at, vol) in enumerate(fx):
            k = n_in(inputs)
            inputs += ["-i", str(f)]
            ms = int(at * 1000)
            filt.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={ms}|{ms}[f{j}]")
            bus.append(f"[f{j}]")
    lufs = float(snd.get("목표음량", -14))
    filt.append(f"{''.join(bus)}amix=inputs={len(bus)}:normalize=0,atrim=0:{total},"
                f"loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[out]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(out / "mix.m4a")])
