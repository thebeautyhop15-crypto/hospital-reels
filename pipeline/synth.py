"""배경음·효과음을 직접 합성 (외부 음원을 쓰지 않으므로 저작권 문제가 없음).

  pad(seconds)   : 잔잔한 코드 진행 배경음 (부드러운 신스 패드 + 은은한 벨)
  whoosh()       : 장면 전환 '휙' 소리
  pop()          : 후킹 등장 '톡' 소리
  shimmer()      : 반짝임 소리 (패널 등장)
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _write(path: Path, x: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(x, -1, 1)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    data = (x * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path


def _env(n: int, a: float, r: float) -> np.ndarray:
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-3)) * np.minimum(1, (n / SR - t) / max(r, 1e-3))
    return np.clip(e, 0, 1)


def _lowpass(x: np.ndarray, k: float) -> np.ndarray:
    """간단한 1차 저역 통과 (k: 0~1, 작을수록 부드러움)."""
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += k * (v - acc)
        y[i] = acc
    return y


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def pad(seconds: float, path: Path, seed: int = 7) -> Path:
    """I–V–vi–IV 진행의 따뜻한 패드 + 펜타토닉 벨 (D 장조, 약 72BPM 느낌)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    chords = [[50, 57, 62, 66, 69], [45, 57, 61, 64, 69], [47, 54, 59, 62, 66], [43, 55, 59, 62, 67]]  # D A Bm G
    bar = 3.3
    for i in range(int(seconds / bar) + 1):
        s, e = int(i * bar * SR), min(n, int((i + 1) * bar * SR + 1.2 * SR))
        if s >= n:
            break
        seg_t = t[s:e] - t[s]
        env = np.minimum(1, seg_t / 1.1) * np.exp(-np.maximum(0, seg_t - bar) * 2.2)
        for j, m in enumerate(chords[i % 4]):
            f = _midi(m)
            det = 1 + (j - 2) * 0.0015
            wave_ = (np.sin(2 * np.pi * f * det * seg_t) + 0.35 * np.sin(2 * np.pi * 2 * f * seg_t + j)
                     + 0.12 * np.sin(2 * np.pi * 3 * f * seg_t))
            pan = 0.5 + (j - 2) * 0.12
            out[s:e, 0] += wave_ * env * (1 - pan) * 0.09
            out[s:e, 1] += wave_ * env * pan * 0.09
    # 벨: 박자마다 펜타토닉 음 하나
    scale = [74, 76, 78, 81, 83, 86]
    beat = bar / 4
    for k in range(int(seconds / beat)):
        if rng.random() < 0.45:
            continue
        s = int(k * beat * SR)
        ln = min(n - s, int(1.6 * SR))
        bt = np.arange(ln) / SR
        f = _midi(scale[rng.integers(len(scale))])
        b = (np.sin(2 * np.pi * f * bt) + 0.3 * np.sin(2 * np.pi * 2.01 * f * bt)) * np.exp(-bt * 3.2) * 0.05
        pan = rng.random()
        out[s:s + ln, 0] += b * (1 - pan)
        out[s:s + ln, 1] += b * pan
    # 부드러운 공간감 (짧은 지연 반복)
    for d, g in ((0.11, 0.25), (0.23, 0.16), (0.37, 0.1)):
        k = int(d * SR)
        out[k:, 0] += out[:-k, 1] * g
        out[k:, 1] += out[:-k, 0] * g
    out *= _env(n, 1.5, 2.0)[:, None]
    return _write(path, out / max(1e-6, np.abs(out).max()) * 0.7)


def whoosh(path: Path, length: float = 0.55) -> Path:
    rng = np.random.default_rng(3)
    n = int(length * SR)
    noise = rng.normal(0, 1, n)
    t = np.arange(n) / SR
    k = 0.02 + 0.25 * np.sin(np.pi * t / length) ** 2           # 필터가 열렸다 닫힘
    y = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc += k[i] * (noise[i] - acc)
        y[i] = acc
    env = np.sin(np.pi * t / length) ** 1.6
    pan = t / length
    out = np.stack([y * env * (1 - pan * 0.6), y * env * (0.4 + pan * 0.6)], axis=1)
    return _write(path, out / np.abs(out).max() * 0.55)


def pop(path: Path) -> Path:
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 18) + 220
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22)
    return _write(path, x * 0.6)


def shimmer(path: Path) -> Path:
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * _midi(m) * t + m) * np.exp(-t * (3 + i)) for i, m in enumerate([86, 90, 93, 98]))
    return _write(path, x / 4 * 0.45 * np.minimum(1, t / 0.01))
