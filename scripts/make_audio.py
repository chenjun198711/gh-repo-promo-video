# -*- coding: utf-8 -*-
"""make_audio.py — 按 script.json 生成火山引擎 TTS 配音，并按时间轴拼成整轨音频。

用法:
    python make_audio.py --dir <项目目录> [--lead 0.35] [--tail 0.35] [--target 50]

读取:
    <dir>/script.json   [{"idx":1,"caption":"画面大字","text":"口播文案"}, ...]
产出:
    <dir>/tts/sNN.mp3     逐段配音（带缓存，改稿只重生成改动段）
    <dir>/wav/sNN.wav
    <dir>/narration.wav   整轨配音
    <dir>/timeline.json   {"durations":[...], "tts":[...], "total":...}

环境变量 VOLC_TTS_KEY 可覆盖内置 Key。
"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import wave

import httpx
import numpy as np

URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def load_api_key():
    """环境变量优先，其次读脚本同目录的 .volc_key（本地文件，已在 .gitignore 中）。"""
    key = (os.environ.get("VOLC_TTS_KEY") or "").strip()
    if key:
        return key
    keyfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".volc_key")
    if os.path.exists(keyfile):
        with open(keyfile, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    raise SystemExit(
        "缺少火山 TTS Key。请任选其一：\n"
        "  1) 设置环境变量 VOLC_TTS_KEY=<你的Key>\n"
        f"  2) 把 Key 写入文件 {keyfile}"
    )


API_KEY = load_api_key()
HEADERS = {
    "X-Api-Key": API_KEY,
    "X-Api-Resource-Id": "seed-tts-2.0",
    "Content-Type": "application/json",
}
SPEAKER = os.environ.get("VOLC_TTS_SPEAKER", "zh_male_cixingjieshuonan_uranus_bigtts")
SR = 48000


def find_ffmpeg():
    """优先 PATH，其次 winget 安装目录。"""
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p, p.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
    import glob
    pat = os.path.expanduser(
        r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_*_8wekyb3d8bbwe"
        r"\ffmpeg-*\bin\ffmpeg.exe")
    hits = glob.glob(pat)
    if not hits:
        raise SystemExit("找不到 ffmpeg，请先 winget install Gyan.FFmpeg")
    ff = hits[0]
    return ff, os.path.join(os.path.dirname(ff), "ffprobe.exe")


FFMPEG, FFPROBE = find_ffmpeg()


def synth(text, out_path, speed_rate, timeout=120, speaker=None):
    body = {
        "user": {"uid": "workbuddy"},
        "req_params": {
            "text": text,
            "speaker": speaker or SPEAKER,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": speed_rate,
            },
        },
    }
    audio = bytearray()
    with httpx.stream("POST", URL, headers=HEADERS, json=body, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        for line in r.iter_lines():
            if not line.strip():
                continue
            j = json.loads(line)
            code = j.get("code")
            if str(code) == "0":
                if j.get("data"):
                    audio.extend(base64.b64decode(j["data"]))
            elif code == 20000000:
                break
            else:
                raise RuntimeError(f"api code={code} msg={j.get('message')}")
    if len(audio) < 500:
        raise RuntimeError("empty audio")
    with open(out_path, "wb") as f:
        f.write(bytes(audio))


async def gen(idx, text, path, sem, speed_rate, speaker=None, force=False):
    if not force and os.path.exists(path) and os.path.getsize(path) > 500:
        print(f"  scene {idx} 命中缓存", flush=True)
        return
    async with sem:
        for attempt in range(4):
            try:
                await asyncio.to_thread(synth, text, path, speed_rate, speaker=speaker)
                print(f"  scene {idx} 生成完成", flush=True)
                return
            except Exception as e:
                print(f"  scene {idx} 重试 {attempt}: {e}", flush=True)
                await asyncio.sleep(2 + attempt * 3)
        raise RuntimeError(f"scene {idx} 生成失败")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--lead", type=float, default=0.35)
    ap.add_argument("--tail", type=float, default=0.35)
    ap.add_argument("--target", type=float, default=50.0, help="目标总时长秒，仅用于提示")
    ap.add_argument("--speed-rate", type=int,
                    default=int(os.environ.get("VOLC_TTS_RATE", 20)),
                    help="火山语速值，20=1.2x")
    ap.add_argument("--speaker", default=os.environ.get("VOLC_TTS_SPEAKER", SPEAKER),
                    help="音色 ID；换音色务必同时加 --force，否则旧音色片段会被静默复用")
    ap.add_argument("--force", action="store_true", help="忽略缓存，全部重新合成")
    args = ap.parse_args()

    base = os.path.abspath(args.dir)
    scenes = json.load(open(os.path.join(base, "script.json"), encoding="utf-8"))
    for d in ("tts", "wav"):
        os.makedirs(os.path.join(base, d), exist_ok=True)

    print(f"[1/3] 合成 {len(scenes)} 段配音（{args.speaker}，语速 "
          f"{1 + args.speed_rate/100:.2f}x{', --force 全量重合成' if args.force else ''}）",
          flush=True)

    async def run():
        sem = asyncio.Semaphore(3)
        await asyncio.gather(*[
            gen(s["idx"], s["text"],
                os.path.join(base, "tts", f"s{s['idx']:02d}.mp3"), sem,
                args.speed_rate, args.speaker, args.force)
            for s in scenes])
    asyncio.run(run())

    print("[2/3] 拼接时间轴", flush=True)
    lead = np.zeros(int(args.lead * SR), dtype=np.float32)
    tail = np.zeros(int(args.tail * SR), dtype=np.float32)
    chunks, durations, tts_durs = [], [], []
    for s in scenes:
        i = s["idx"]
        wav = os.path.join(base, "wav", f"s{i:02d}.wav")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i",
                        os.path.join(base, "tts", f"s{i:02d}.mp3"),
                        "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav],
                       check=True)
        with wave.open(wav, "rb") as w:
            d = np.frombuffer(w.readframes(w.getnframes()),
                              dtype=np.int16).astype(np.float32) / 32768.0
        chunks.append(np.concatenate([lead, d, tail]))
        durations.append((len(lead) + len(d) + len(tail)) / SR)
        tts_durs.append(len(d) / SR)
        print(f"  scene {i}: 配音 {len(d)/SR:5.2f}s  画面 {durations[-1]:5.2f}s", flush=True)

    total = np.concatenate(chunks)
    peak = float(np.max(np.abs(total)))
    if peak > 0.97:
        total *= 0.97 / peak
    with wave.open(os.path.join(base, "narration.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((total * 32767).astype(np.int16).tobytes())

    tot = len(total) / SR
    with open(os.path.join(base, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump({"durations": durations, "tts": tts_durs, "total": float(tot)},
                  f, ensure_ascii=False, indent=1)

    print(f"[3/3] 总时长 {tot:.2f}s ({tot/60:.2f} min)  目标 {args.target:.0f}s")
    if tot > args.target + 3:
        over = tot - args.target
        print(f"  偏长 {over:.1f}s — 建议删减最长的那 2~3 段文案后重跑（仅改动段会重新合成）")


if __name__ == "__main__":
    main()
