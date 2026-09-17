# -*- coding: utf-8 -*-
"""make_soundtrack.py — 给推广片合成背景音乐(BGM)与转场音效，并与配音混成一轨。

用法:
    python make_soundtrack.py --dir <项目目录>
        [--bgm-gain 1.0] [--sfx-gain 1.0] [--no-bgm] [--no-sfx] [-o 输出wav]

读取:
    <dir>/narration.wav    配音整轨（make_audio.py 产出）
    <dir>/timeline.json    各场景时长（用于定位转场点）
产出:
    <dir>/soundtrack.wav   立体声混音轨（配音 + BGM + 音效），make_video.py 会自动优先使用

设计要点:
  * BGM 与音效全部用 numpy 程序化合成，不需要任何素材文件，无版权风险。
  * BGM 是 Am7-Fmaj7-Cmaj7-G6 的四小节循环，84 BPM，含 pad / sub bass / 软 kick / 沙锤；
    整体低通到 7kHz，给口播让出中高频空间。
  * 人声侧链闪避(ducking)：按配音包络动态压低 BGM（说话时约 -21dB，间隙抬回 -12dB）。
  * 音效在场景切换处：whoosh 转场 + 轻 blip；末屏 CTA 前加 riser + impact。
  * 音量可用 --bgm-gain / --sfx-gain 微调；--no-bgm / --no-sfx 可单独关闭。
"""
import argparse
import json
import os
import wave

import numpy as np

SR = 48000


# ---------------------------------------------------------------- 基础工具
def midi_f(m):
    """MIDI 音高 → 频率(Hz)。"""
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def fft_filter(x, sr, lo=None, hi=None, order=2):
    """零相位 Butterworth 式低通/高通/带通（频域实现，向量化，无递归）。"""
    n = len(x)
    if n == 0:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    H = np.ones_like(f)
    fc = np.maximum(f, 1e-6)
    if lo:
        H *= 1.0 / np.sqrt(1.0 + (lo / fc) ** (2 * order))
    if hi:
        H *= 1.0 / np.sqrt(1.0 + (fc / hi) ** (2 * order))
    return np.fft.irfft(X * H, n=n)


def bell_env(n, sr, attack, release):
    """梯形包络，两端用升余弦平滑（无爆音）。"""
    env = np.ones(n, dtype=np.float64)
    a = min(int(attack * sr), n // 2)
    r = min(int(release * sr), n // 2)
    if a > 1:
        env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if r > 1:
        env[n - r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    return env


def ensure(x, n):
    """把音块补齐/裁剪到 n 个采样。"""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x))])


def add_at(track, sig, at_sec, gain=1.0):
    """把音块叠加到 track 的 at_sec 处（越界自动裁剪）。"""
    s = int(round(at_sec * SR))
    if s >= len(track):
        return
    off = 0
    if s < 0:
        off = -s
        s = 0
    seg = sig[off:]
    e = min(len(track), s + len(seg))
    if e <= s:
        return
    track[s:e] += gain * seg[: e - s]


# ---------------------------------------------------------------- 音色单元
def pad_note(freq, n, sr, amp, attack, release):
    """柔和 pad：基频 + 泛音 + 两层失谐，营造厚实的合成弦垫。"""
    t = np.arange(n) / sr
    w = np.sin(2 * np.pi * freq * t)
    w += 0.30 * np.sin(2 * np.pi * freq * 2 * t + 0.4)
    w += 0.10 * np.sin(2 * np.pi * freq * 3 * t + 1.2)
    w += 0.60 * np.sin(2 * np.pi * freq * 1.0016 * t + 1.7)
    w += 0.60 * np.sin(2 * np.pi * freq * 0.9984 * t + 2.9)
    w /= 2.60
    return amp * w * bell_env(n, sr, attack, release)


def bass_note(freq, n, sr, amp):
    t = np.arange(n) / sr
    w = np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(2 * np.pi * freq * 2 * t)
    w /= 1.18
    return amp * w * bell_env(n, sr, 0.012, min(0.45, n / sr * 0.45))


def kick(sr, rng, amp=1.0, f=55.0, tau=0.085, dur=0.42):
    """软底鼓：音高快速下坠的正弦 + 极短噪声点击。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f_env = f * (1.0 + 1.8 * np.exp(-t / 0.012))
    ph = 2 * np.pi * np.cumsum(f_env) / sr
    body = np.sin(ph) * np.exp(-t / tau)
    click = rng.standard_normal(n) * np.exp(-t / 0.004) * 0.10
    return amp * (body + click) * 0.9


def hat(sr, rng, amp=1.0, tau=0.022, dur=0.09):
    """沙锤：高通白噪声 + 极快衰减。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    nz = fft_filter(rng.standard_normal(n), sr, lo=5500, order=2)
    return amp * nz * np.exp(-t / tau)


def pluck(freq, sr, amp=1.0, tau=0.16, dur=0.34):
    """拨弦式音色（琶音用）：快速起音 + 指数衰减。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    w = (np.sin(2 * np.pi * freq * t)
         + 0.35 * np.sin(2 * np.pi * freq * 2 * t)
         + 0.12 * np.sin(2 * np.pi * freq * 3.01 * t))
    w /= 1.47
    return amp * w * (1.0 - np.exp(-t / 0.004)) * np.exp(-t / tau)


# ---------------------------------------------------------------- 音效
def whoosh(dur=0.60, seed=11, amp=1.0, rise=True):
    """转场 whoosh：四个噪声频带依次错峰，制造扫频的"唰"感。"""
    n = int(dur * SR)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    bands = [(None, 500), (500, 1500), (1500, 3500), (3500, None)]
    if rise:
        # 高频带最晚到位 → 听感是"扫上去"；峰值落在 0.58 处，正好压在画面切换点
        peaks = [0.05, 0.22, 0.42, 0.58]
    else:
        peaks = [0.58, 0.42, 0.22, 0.05]
    weights = [0.55, 0.95, 0.85, 0.60]
    pos = np.arange(n) / n
    out = np.zeros(n)
    for (lo, hi), p, w in zip(bands, peaks, weights):
        env = np.exp(-(((pos - p) / 0.24) ** 2))
        out += w * fft_filter(noise, SR, lo=lo, hi=hi) * env
    out *= np.exp(-pos / 0.62) * (1.0 - np.exp(-pos / 0.035))
    return amp * out / (np.max(np.abs(out)) + 1e-9)


def impact(dur=1.1, seed=23, amp=1.0):
    """落地感冲击：低频下潜 + 短噪声爆。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    low = np.sin(2 * np.pi * 58 * t) * np.exp(-t / 0.16)
    sub = np.sin(2 * np.pi * 37 * t) * np.exp(-t / 0.32)
    nz = fft_filter(rng.standard_normal(n), SR, lo=220, hi=6000) * np.exp(-t / 0.085)
    out = 0.55 * low + 0.50 * sub + 0.22 * nz
    return amp * out / (np.max(np.abs(out)) + 1e-9)


def riser(dur=1.5, seed=31, amp=1.0):
    """上行 riser：指数扫频正弦 + 渐强噪声，用于末屏 CTA 前铺垫。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    T = dur
    f0, f1 = 170.0, 1400.0
    k = np.log(f1 / f0)
    phase = 2 * np.pi * f0 * T / k * (np.exp(k * t / T) - 1.0)
    env = (t / T) ** 1.8
    rng = np.random.default_rng(seed)
    nz = fft_filter(rng.standard_normal(n), SR, lo=600, hi=7000) * env * 0.35
    out = (np.sin(phase) * env * 0.7) + nz
    out *= 1.0 - np.exp(-t / 0.05)
    return amp * out / (np.max(np.abs(out)) + 1e-9)


def blip(sr, amp=1.0, f=1500.0, tau=0.020, dur=0.10):
    """轻提示音：短促双击，标记画面切换。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    w = np.sin(2 * np.pi * f * t) + 0.40 * np.sin(2 * np.pi * f * 2.02 * t)
    return amp * w * np.exp(-t / tau) / 1.4


# ---------------------------------------------------------------- BGM 编曲
CHORDS = [[57, 60, 64, 67],    # Am7
          [53, 57, 60, 64],    # Fmaj7
          [52, 55, 59, 64],    # Cmaj7
          [50, 55, 59, 64]]    # G6
BASSD = [45, 41, 48, 43]       # A2 F2 C3 G2
ARP_PAT = [[0, 3, 2, 5, 4, 6], [1, 4, 3, 6, 5, 7], [2, 5, 4, 7, 6, 9], [0, 4, 3, 6, 2, 7]]


def make_bgm(total_n, sr, seed=7, bpm=84.0):
    """生成总长 total_n 的场景化 BGM（弦垫 + 低音 + 软鼓 + 沙锤 + 琶音）。"""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = 4 * beat
    bar_n = int(round(bar * sr))
    n_bars = total_n // bar_n + 3
    buf = np.zeros(n_bars * bar_n + bar_n, dtype=np.float64)

    for b in range(n_bars):
        t0 = b * bar
        ch = CHORDS[b % 4]
        root = BASSD[b % 4]

        # 弦垫：整小节长音，跨小节交叠，衔接自然
        pn = int(bar * 1.18 * sr)
        for m in ch:
            add_at(buf, pad_note(midi_f(m), pn, sr, 0.062, 0.85, 1.30), t0)
        # 中音区层（+12 半音）：手机外放才听得到音乐——低音区在手机喇叭上会消失
        for m in (ch[1], ch[2]):
            add_at(buf, pad_note(midi_f(m + 12), pn, sr, 0.030, 1.00, 1.30), t0)
        # 高八度微光层，提亮但不抢耳
        add_at(buf, pad_note(midi_f(ch[2] + 12), pn, sr, 0.018, 1.10, 1.30), t0)

        # 低音：每小节两下（1、3 拍）
        bn = int(beat * 1.9 * sr)
        for k in (0, 2):
            add_at(buf, bass_note(midi_f(root), bn, sr, 0.20), t0 + k * beat)

        # 鼓组：软底鼓 1、3 拍；沙锤 8 分音符（给口播留出中高频，但律动要清楚）
        for k in (0, 2):
            add_at(buf, kick(sr, rng, 0.38), t0 + k * beat)
        for k in range(8):
            if rng.random() < 0.30:
                continue
            add_at(buf, hat(sr, rng, 0.042 + 0.014 * rng.random()),
                   t0 + k * beat / 2)

        # 琶音：中音区点缀，是音乐在人声频段之外唯一"活着"的线条
        for j, step in enumerate(ARP_PAT[b % 4] + [1, 5]):
            pos = step / 2.0
            if pos >= 4.0:
                continue
            note = ch[j % len(ch)] + 24
            add_at(buf, pluck(midi_f(note), sr, 0.10), t0 + pos * beat)

    buf = buf[:total_n]
    # 音色塑形：去掉超低频轰鸣，低通到 7kHz 给口播让路
    buf = fft_filter(buf, sr, lo=42, hi=7000, order=2)
    # 首尾淡入淡出
    fi = int(0.9 * sr)
    fo = int(1.6 * sr)
    if len(buf) > fi + fo:
        buf[:fi] *= np.linspace(0, 1, fi) ** 1.4
        buf[-fo:] *= np.linspace(1, 0, fo) ** 1.4
    return buf / (np.max(np.abs(buf)) + 1e-9)


# ---------------------------------------------------------------- 人声闪避
def speech_gate(narration, sr, hop=0.010):
    """返回与 narration 等长的 0~1 包络：说话≈1，静默≈0（快起慢落）。"""
    a = np.abs(narration)
    h = int(hop * sr)
    nf = max(len(a) // h, 1)
    seg = a[: nf * h].reshape(nf, h)
    rms = np.sqrt((seg ** 2).mean(axis=1))

    atk = 1.0 - np.exp(-hop / 0.018)
    rel = 1.0 - np.exp(-hop / 0.300)
    g = np.empty(nf)
    prev = 0.0
    for i in range(nf):
        c = atk if rms[i] > prev else rel
        prev += c * (rms[i] - prev)
        g[i] = prev

    hi = np.percentile(g, 92) if np.any(g > 0) else 1.0
    if hi > 1e-6:
        g = np.clip(g / hi, 0.0, 1.0)
    # 提升对比：让静默段真正放开
    g = g ** 1.6
    # 帧率(100Hz) 线性插值回音频采样率，避免增益突变产生咔哒声
    centers = (np.arange(nf) + 0.5) * h
    return np.interp(np.arange(len(a), dtype=np.float64), centers, g)


def db(x, ref=1.0):
    return 20.0 * np.log10(max(x, 1e-9) / ref)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--bgm-gain", type=float, default=1.0)
    ap.add_argument("--sfx-gain", type=float, default=1.0)
    ap.add_argument("--no-bgm", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--bpm", type=float, default=84.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--gain-db", type=float, default=0.0,
                    help="整轨总增益 dB（觉得偏轻/偏响时微调，正数变响）")
    ap.add_argument("--dump-stems", action="store_true",
                    help="额外导出 stems/voice|bgm|sfx.wav，便于单独试听与调音")
    args = ap.parse_args()

    base = os.path.abspath(args.dir)
    nar_p = os.path.join(base, "narration.wav")
    if not os.path.exists(nar_p):
        raise SystemExit("找不到 narration.wav，请先运行 make_audio.py")

    with wave.open(nar_p, "rb") as w:
        nch, sw_, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw_ != 2:
        raise SystemExit("narration.wav 需为 16bit PCM")
    nar = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    if nch > 1:
        nar = nar.reshape(-1, nch).mean(axis=1)
    if fr != SR:
        raise SystemExit(f"narration.wav 采样率 {fr}，需 {SR}")
    n = len(nar)
    dur = n / SR

    # 场景边界（供音效定位）
    tl_p = os.path.join(base, "timeline.json")
    durs = json.load(open(tl_p, encoding="utf-8"))["durations"] if os.path.exists(tl_p) else []
    starts, acc = [], 0.0
    for d in durs:
        starts.append(acc)
        acc += d

    print(f"[1/4] 配音 {dur:.2f}s / {len(durs)} 场景", flush=True)

    # ---- BGM
    music = np.zeros(n)
    if not args.no_bgm:
        print(f"[2/4] 合成 BGM（{args.bpm:.0f} BPM 四小节循环）", flush=True)
        music = make_bgm(n, SR, seed=args.seed, bpm=args.bpm)

    # ---- 音效
    sfx = np.zeros(n)
    if not args.no_sfx:
        print("[3/4] 布置转场音效", flush=True)
        events = []
        # 开场：轻声上行 whoosh，自然带出第一句
        events.append((whoosh(dur=0.55, seed=41, amp=0.28), 0.00))
        # 每个场景边界：whoosh 提前 0.30s 扬起，blip 落在画面切换点
        for i, st in enumerate(starts):
            if i == 0:
                continue
            last = (i == len(starts) - 1)
            if last:
                events.append((riser(dur=1.4, seed=51, amp=0.42), max(st - 1.35, 0.0)))
                events.append((impact(amp=0.46), max(st - 0.02, 0.0)))
            events.append((whoosh(dur=0.60, seed=60 + i, amp=0.34), max(st - 0.34, 0.0)))
            events.append((blip(SR, amp=0.13), st + 0.02))
        for sig, at in events:
            add_at(sfx, sig, at, 1.0)
        print(f"  共 {len(events)} 个音效事件", flush=True)
    else:
        print("[3/4] 跳过音效", flush=True)

    # ---- 闪避 + 混音
    print("[4/4] 人声闪避 + 混音", flush=True)
    gate = speech_gate(nar, SR)
    G_GAP, G_SPEECH = 0.30, 0.042          # BGM 在静默/说话时的增益（相对满幅 BGM）
    bgm_gain = (G_GAP + (G_SPEECH - G_GAP) * gate) * args.bgm_gain
    music_ducked = music * bgm_gain
    sfx_scaled = sfx * args.sfx_gain

    # 右声道用 9ms 哈斯延迟拓宽 BGM 声场（配音与音效保持居中）
    dly = int(0.009 * SR)
    music_r = np.concatenate([np.zeros(dly), music_ducked])[:n]

    left = nar + music_ducked + sfx_scaled
    right = nar + music_r + sfx_scaled

    if args.gain_db:
        g = 10 ** (args.gain_db / 20.0)
        left *= g
        right *= g

    peak = max(np.max(np.abs(left)), np.max(np.abs(right)))
    if peak > 0.985:
        k = 0.985 / peak
        left *= k
        right *= k
        print(f"  峰值 {peak:.3f} 超限，整体衰减 {db(1/k):+.1f}dB", flush=True)

    # ---- 质检统计
    sp = gate > 0.5
    gp = gate < 0.15
    rep = [
        f"  配音 RMS  {db(np.sqrt((nar[sp] ** 2).mean()) if np.any(sp) else 0):6.1f} dBFS（说话段）",
        f"  BGM 说话时 {db(np.sqrt((music_ducked[sp] ** 2).mean()) if np.any(sp) else 0):6.1f} dBFS"
        f" / 间隙时 {db(np.sqrt((music_ducked[gp] ** 2).mean()) if np.any(gp) else 0):6.1f} dBFS",
        f"  成品峰值 {db(peak):6.1f} dBFS",
    ]
    for line in rep:
        print(line, flush=True)

    out = args.out or os.path.join(base, "soundtrack.wav")
    inter = np.stack([left, right], axis=1)
    inter = np.clip(inter, -1.0, 1.0)
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((inter * 32767).astype(np.int16).tobytes())
    print(f"\n混音轨: {out}")
    print(f"  {dur:.2f}s  48kHz 立体声  {os.path.getsize(out)/1024/1024:.2f}MB")

    if args.dump_stems:
        sdir = os.path.join(base, "stems")
        os.makedirs(sdir, exist_ok=True)
        for name, data in (("voice", nar), ("bgm", music_ducked), ("sfx", sfx_scaled)):
            p = os.path.join(sdir, f"{name}.wav")
            with wave.open(p, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())
        print(f"  分轨: {sdir}\\voice.wav | bgm.wav | sfx.wav")


if __name__ == "__main__":
    main()
