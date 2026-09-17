# -*- coding: utf-8 -*-
"""verify_soundtrack.py — 客观校验混音轨：节拍结构 / 闪避深度 / 音效落点 / 频谱分布。

用法: python verify_soundtrack.py <项目目录>
需要先跑 make_soundtrack.py --dump-stems 生成 stems/。

模型无法试听时用这个脚本代替耳朵：确认 BGM 真有节拍与小节呼吸、
闪避深度在合理区间、音效确实落在场景切换点上、各频段没人声/音乐互相打架。
"""
import json
import os
import sys
import wave

import numpy as np

SR = 48000
FR = 0.010  # 包络帧长(秒)


def db(x):
    return 20 * np.log10(max(float(x), 1e-9))


def rd(path):
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        a = np.frombuffer(w.readframes(w.getnframes()),
                          dtype=np.int16).astype(np.float64) / 32768.0
    return a.reshape(-1, nch).mean(axis=1) if nch > 1 else a


def env(x, win=FR):
    """按 win 秒计算 RMS 包络。"""
    h = int(win * SR)
    nf = len(x) // h
    return np.sqrt((x[: nf * h].reshape(nf, h) ** 2).mean(axis=1))


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    sdir = os.path.join(base, "stems")
    if not os.path.isdir(sdir):
        raise SystemExit("缺少 stems/，请先运行 make_soundtrack.py --dump-stems")

    voice = rd(os.path.join(sdir, "voice.wav"))
    bgm = rd(os.path.join(sdir, "bgm.wav"))
    sfx = rd(os.path.join(sdir, "sfx.wav"))
    st = rd(os.path.join(base, "soundtrack.wav"))

    tl = json.load(open(os.path.join(base, "timeline.json"), encoding="utf-8"))
    starts, acc = [], 0.0
    for d in tl["durations"]:
        starts.append(round(acc, 2))
        acc += d

    print(f"总长 {len(st)/SR:.2f}s / {len(starts)} 场景")
    print(f"场景起点 {starts}")
    print()

    vh, bh, sh_ = env(voice), env(bgm), env(sfx)

    # ---- 1. 节拍结构：对 BGM 包络做自相关
    e = bh - bh.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    ac /= ac[0] + 1e-9
    lo, hi = int(0.30 / FR), int(3.2 / FR)
    beat_lag, bar_lag = int(0.714 / FR), int(2.857 / FR)
    pk = lo + int(np.argmax(ac[lo:hi]))
    print("[1] BGM 节拍（自相关，84BPM：拍 0.714s / 半小节 1.429s / 小节 2.857s）")
    print(f"      全局主峰 {pk*FR:.3f}s -> {ac[pk]:+.3f}")
    print(f"      拍点 0.714s -> {ac[beat_lag]:+.3f}  （底鼓落在 1、3 拍时此处应为负，属半拍律动）")
    print(f"      半小节 1.429s -> {ac[int(1.429/FR)]:+.3f}  （底鼓脉冲）")
    print(f"      小节 2.857s -> {ac[bar_lag]:+.3f}  （和弦呼吸）")
    pulse = ac[int(1.429 / FR)]
    swing = ac[bar_lag]
    ok = pulse > 0.06 and swing > 0.10
    print(f"      判定：{'律动成立（有鼓点脉冲 + 小节呼吸）' if ok else '律动偏弱，建议加大 kick 或收紧 pad'}")
    print()

    # ---- 2. 闪避深度
    g = vh / (np.percentile(vh, 92) + 1e-9)
    sp, gp = g > 0.5, g < 0.15
    vs, bs = np.sqrt((vh[sp] ** 2).mean()), np.sqrt((bh[sp] ** 2).mean())
    bg = np.sqrt((bh[gp] ** 2).mean())
    print("[2] 闪避（人声侧链）")
    print(f"      说话时：人声 {db(vs):.1f} dBFS，BGM {db(bs):.1f} dBFS"
          f"  → 音乐低于人声 {db(vs)-db(bs):.1f} dB")
    print(f"      间隙时：BGM {db(bg):.1f} dBFS  → 抬起 {db(bg)-db(bs):.1f} dB")
    depth = db(bg) - db(bs)
    print(f"      判定：{'合理（4~12dB 抬升）' if 4 <= depth <= 12 else '偏离常见区间，建议微调'}")
    print()

    # ---- 3. 音效落点
    thr = 0.02
    hits, i = [], 0
    while i < len(sh_):
        if sh_[i] > thr:
            j = i
            while j < len(sh_) and sh_[j] > thr * 0.35:
                j += 1
            hits.append((i * FR, j * FR, float(sh_[i:j].max())))
            i = j + 5
        else:
            i += 1
    print(f"[3] 音效 {len(hits)} 处（应对齐场景切换）")
    for t0, t1, pv in hits:
        tag = ""
        for k, s in enumerate(starts):
            if abs(t0 - s) < 0.12:
                tag = f"场景{k+1} 切换点"
            elif abs(t0 - (s - 0.30)) < 0.15:
                tag = f"场景{k+1} 转场 whoosh"
            elif abs(t0 - (s - 1.35)) < 0.25:
                tag = f"场景{k+1} CTA riser"
        print(f"      {t0:6.2f} → {t1:6.2f}s  峰值 {db(pv):6.1f} dB  {tag}")
    print()

    # ---- 4. 频谱分布（每个信号只做一次 FFT）
    def spec(x):
        return np.abs(np.fft.rfft(x * np.hanning(len(x)))) / len(x)

    freq = np.fft.rfftfreq(len(st), 1 / SR)
    specs = {"成品": spec(st), "配音": spec(voice), "BGM": spec(bgm), "音效": spec(sfx)}
    bands = [(20, 120, "超低"), (120, 400, "低"), (400, 1200, "中低"),
             (1200, 4000, "中高"), (4000, 9000, "高"), (9000, 16000, "极高")]
    print("[4] 频谱分布 (dBFS)")
    print("      频段        成品     配音      BGM      音效")
    for a, b, nm in bands:
        m = (freq >= a) & (freq < b)
        row = [db(np.sqrt((specs[k][m] ** 2).sum())) for k in specs]
        print(f"      {nm:<8s} " + "  ".join(f"{v:7.1f}" for v in row))
    # 手机外放可听性：手机喇叭基本不还原 400Hz 以下，音乐若只剩低频等于没有音乐
    m = (freq >= 400) & (freq < 4000)
    bm = db(np.sqrt((specs["BGM"][m] ** 2).sum()))
    vm = db(np.sqrt((specs["配音"][m] ** 2).sum()))
    print(f"\n[4b] 手机外放检验（400-4000Hz 中音区）：BGM {bm:.1f} / 配音 {vm:.1f} dBFS"
          f" → 低 {vm-bm:.1f} dB")
    print(f"      判定：{'音乐在小喇叭上仍可听' if vm - bm < 26 else 'BGM 中音区过弱，手机外放会听不到音乐'}")
    print()
    print(f"[5] 成品：峰值 {db(np.max(np.abs(st))):.1f} dBFS，"
          f"整体 RMS {db(np.sqrt((st**2).mean())):.1f} dBFS")
    if np.max(np.abs(st)) > 0.999:
        print("      警告：存在削波")


if __name__ == "__main__":
    main()
