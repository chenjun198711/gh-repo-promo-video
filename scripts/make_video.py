# -*- coding: utf-8 -*-
"""make_video.py — GitHub 项目推广片渲染（9:16 竖屏 / 16:9 横屏）。

用法:
    python make_video.py --dir <项目目录> [--ratio 16:9] [--fps 30] [--out <输出mp4>]

读取:
    <dir>/repo.json       仓库元数据（owner/name/stars/language/license）
    <dir>/script.json     分镜（idx/caption/kicker/asset/text）
    <dir>/timeline.json   各场景时长（make_audio.py 产出）
    <dir>/narration.wav   整轨配音（有 soundtrack.wav 时优先用后者）
产出:
    <dir>/slides/sNN.png  逐场景画面
    <dir>/clips/vNN.mp4
    <dir>/cover_16x9.png / cover_youtube_1280x720.jpg / cover_9x16.png
                          B站/YouTube 风格封面（由 make_cover.py 生成）
    <dir>/final.mp4       成片

字幕支持 **高亮** 标记，渲染为强调色 + 光晕。
"""
import argparse
import glob
import hashlib
import json
import math
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def find_ffmpeg():
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p, os.path.join(os.path.dirname(p), "ffprobe.exe")
    pat = os.path.expanduser(
        r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_*_8wekyb3d8bbwe"
        r"\ffmpeg-*\bin\ffmpeg.exe")
    hits = glob.glob(pat)
    if not hits:
        raise SystemExit("找不到 ffmpeg，请先 winget install Gyan.FFmpeg")
    ff = hits[0]
    return ff, os.path.join(os.path.dirname(ff), "ffprobe.exe")


FFMPEG, FFPROBE = find_ffmpeg()


def probe_duration(path):
    """读取媒体时长（秒），失败返回 0。"""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# GitHub 暗色配色
BG_TOP = (13, 17, 23)
BG_BOT = (22, 27, 34)
ACCENT = (46, 160, 67)
ACCENT_HL = (110, 231, 148)          # 字幕高亮色
GOLD = (240, 200, 60)
TEXT = (230, 237, 243)
TEXT_GRAD = ((247, 251, 254), (176, 191, 208))   # 正文竖向渐变（上亮下暗）
MUTED = (139, 148, 158)
BORDER = (48, 54, 61)

F_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
F_REG = r"C:\Windows\Fonts\msyh.ttc"

# 中文禁则：这些标点不能落在行首
NO_START = "，。、；：？！）】》」』…·%,.;:?!)]}”’"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(F_REG, size)


def layout(W, H):
    """按画幅返回版式坐标。竖屏 9:16 与横屏 16:9 是两套布局。"""
    if H >= W:
        return {
            "portrait": True, "pad": 64, "topbar_y": 78, "avatar": 62,
            "media_x": 0, "media_y": 232, "media_w": W, "media_h": 918,
            "media_radius": 0, "media_inset": (120, 60),
            "in_frame": False,                 # 竖屏：标签在画面下方
            "kicker_x": None, "kicker_y": 1216,
            "cap_x": 0, "cap_y": 1292, "cap_size": 62, "cap_lh": 84,
            "cap_align": "center", "cap_bar": False,
            "rail_y": None,
        }
    pad = 0
    return {
        "portrait": False, "pad": 140, "topbar_y": 44, "avatar": 60,
        "media_x": 0, "media_y": 142, "media_w": W, "media_h": 736,
        "media_radius": 0, "media_inset": (240, 74),
        "in_frame": True,                  # 横屏：标签/星标/页码放进画面内
        "kicker_x": 140, "kicker_y": 142 + 736 - 40 - 54,
        "cap_x": 152, "cap_y": 902, "cap_size": 58, "cap_lh": 78,
        "cap_align": "left", "cap_bar": True,
        "rail_y": H - 30,
    }


def make_bg(W, H):
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-W * 0.35, -H * 0.32, W * 1.05, H * 0.24], fill=(20, 72, 44))
    # 模糊掉椭圆边缘，避免在媒体区上沿出现一条硬边
    glow = glow.filter(ImageFilter.GaussianBlur(max(int(H * 0.055), 12)))
    return Image.blend(bg, glow, 0.5)


# ---------------------------------------------------------------- 文字效果

def draw_text_soft(canvas, xy, text, fnt, fill=None, *, grad=None,
                   shadow_alpha=200, shadow_blur=11, shadow_dy=4,
                   glow=None, glow_alpha=100, glow_blur=18):
    """带柔和投影 / 光晕 / 竖向渐变填充的文字。

    grad=(顶色, 底色) 时用渐变填充并忽略 fill；glow 给出发光颜色。
    """
    if not text:
        return
    x, y = int(round(xy[0])), int(round(xy[1]))
    pad = int(max(glow_blur, shadow_blur) * 2 + 8)
    size = int(getattr(fnt, "size", 40))
    tw = int(fnt.getlength(text)) + 2 * pad + 4
    th = int(size * 1.75) + 2 * pad + 4
    ox = oy = pad + 2

    if glow:
        gl = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ImageDraw.Draw(gl).text((ox, oy), text, font=fnt,
                                fill=tuple(glow) + (glow_alpha,))
        gl = gl.filter(ImageFilter.GaussianBlur(glow_blur))
        canvas.paste(gl, (x - ox, y - oy), gl)

    if shadow_alpha:
        sh = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((ox, oy), text, font=fnt, fill=(0, 0, 0, shadow_alpha))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
        canvas.paste(sh, (x - ox, y - oy + shadow_dy), sh)

    if grad:
        mask = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(mask).text((ox, oy), text, font=fnt, fill=255)
        g = Image.new("RGB", (tw, th))
        gd = ImageDraw.Draw(g)
        for yy in range(th):
            t = yy / (th - 1)
            gd.line([(0, yy), (tw, yy)],
                    fill=tuple(int(grad[0][i] + (grad[1][i] - grad[0][i]) * t)
                               for i in range(3)))
        canvas.paste(g, (x - ox, y - oy), mask)
    else:
        ImageDraw.Draw(canvas).text((x, y), text, font=fnt, fill=fill)


def paste_rrect(canvas, box, radius, fill_rgba, outline=None, width=2):
    """在 RGB 画布上贴一个可半透明的圆角矩形。"""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius,
                                        fill=fill_rgba, outline=outline, width=width)
    canvas.paste(ov, (x0, y0), ov)


def paste_scrim(canvas, x, y, w, h, alpha0=0, alpha1=215, power=1.35):
    """自下而上的暗色渐变蒙版（给画面内文字做底）。"""
    sc = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(sc)
    for i in range(h):
        t = (i / (h - 1)) ** power
        d.line([(0, i), (w, i)], fill=(5, 8, 12, int(alpha0 + (alpha1 - alpha0) * t)))
    canvas.paste(sc, (x, y), sc)


def pill_metrics(canvas, text, fnt, pad_x=28, pad_y=13, min_h=50):
    d = ImageDraw.Draw(canvas)
    size = int(getattr(fnt, "size", 28))
    return (int(d.textlength(text, font=fnt) + 2 * pad_x),
            max(int(size + 2 * pad_y), min_h))


def draw_pill_at(canvas, x, y, text, fnt, w, h, *, radius=None,
                 fill=(18, 44, 31, 232), outline=(52, 140, 84),
                 txt_fill=(126, 233, 155), glow=False):
    """在给定位置画胶囊，尺寸由外部算好（便于右对齐）。"""
    r = radius if radius is not None else h // 2
    if glow:
        gl = Image.new("RGBA", (w + 96, h + 96), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle([48, 48, w + 48, h + 48],
                                             radius=r + 10, fill=(34, 104, 62, 130))
        gl = gl.filter(ImageFilter.GaussianBlur(20))
        canvas.paste(gl, (int(x) - 48, int(y) - 48), gl)
    paste_rrect(canvas, (x, y, x + w, y + h), r, fill, outline, 2)
    size = int(getattr(fnt, "size", 28))
    draw_text_soft(canvas, (x + (w - ImageDraw.Draw(canvas).textlength(text, font=fnt)) / 2,
                            y + (h - size) / 2 - size * 0.17),
                   text, fnt, txt_fill, shadow_alpha=120, shadow_blur=6, shadow_dy=2)


def draw_pill(canvas, x, y, text, fnt, **kw):
    """左上角定位的胶囊，返回 (宽, 高)。"""
    pad_x, pad_y = kw.get("pad_x", 28), kw.get("pad_y", 13)
    min_h = kw.get("min_h", 50)
    w, h = pill_metrics(canvas, text, fnt, pad_x, pad_y, min_h)
    draw_pill_at(canvas, x, y, text, fnt, w, h,
                 radius=kw.get("radius"), fill=kw.get("fill", (18, 44, 31, 232)),
                 outline=kw.get("outline", (52, 140, 84)),
                 txt_fill=kw.get("txt_fill", (126, 233, 155)),
                 glow=kw.get("glow", False))
    return w, h


def draw_pill_right(canvas, right_x, y, text, fnt, **kw):
    """右对齐定位的胶囊，返回 (宽, 高)。"""
    pad_x, pad_y = kw.get("pad_x", 28), kw.get("pad_y", 13)
    min_h = kw.get("min_h", 50)
    w, h = pill_metrics(canvas, text, fnt, pad_x, pad_y, min_h)
    draw_pill_at(canvas, right_x - w, y, text, fnt, w, h,
                 radius=kw.get("radius"), fill=kw.get("fill", (18, 44, 31, 232)),
                 outline=kw.get("outline", (52, 140, 84)),
                 txt_fill=kw.get("txt_fill", (126, 233, 155)),
                 glow=kw.get("glow", False))
    return w, h


# ---------------------------------------------------------------- 富文本折行

def parse_rich(text):
    """把 **高亮** 解析为 (纯文本, 每字符是否高亮)。"""
    plain, flags, hl = [], [], False
    i = 0
    while i < len(text):
        if text[i:i + 2] == "**":
            hl = not hl
            i += 2
            continue
        plain.append(text[i])
        flags.append(hl)
        i += 1
    return "".join(plain), flags


def wrap_ranges(draw, text, fnt, max_w):
    """按宽度折行（含中文禁则），返回 [(start, end)]。"""
    n, lines, start = len(text), [], 0
    while start < n:
        end, w = start, 0.0
        while end < n:
            if text[end] == "\n":
                break
            adv = draw.textlength(text[end], font=fnt)
            if w + adv > max_w and end > start:
                break
            w += adv
            end += 1
        # 禁则：行首若为收尾标点，把前一个字符一起挪到下一行
        while end < n and end - start > 1 and text[end] in NO_START \
                and text[end - 1] not in " \u3000":
            end -= 1
        lines.append((start, end))
        start = end
        if start < n and text[start] == "\n":
            start += 1
    # 末行过短（1~2 字）时，从前一行借一个字符，避免出现孤字行
    if len(lines) >= 2:
        s, e = lines[-1]
        ps, pe = lines[-2]
        if e - s <= 2 and pe - ps > 3:
            lines[-2] = (ps, pe - 1)
            lines[-1] = (pe - 1, e)
    return lines


def draw_rich_line(canvas, x, y, text, flags, a, b, fnt, *, hl_color=ACCENT_HL,
                   base_grad=TEXT_GRAD):
    """按高亮标记逐段绘制一行文字。"""
    cx, i = x, a
    while i < b:
        hl = flags[i]
        j = i
        while j < b and flags[j] == hl:
            j += 1
        seg = text[i:j]
        if hl:
            draw_text_soft(canvas, (cx, y), seg, fnt, hl_color,
                           glow=hl_color, glow_alpha=115, glow_blur=17,
                           shadow_alpha=210, shadow_blur=10, shadow_dy=3)
        else:
            draw_text_soft(canvas, (cx, y), seg, fnt, grad=base_grad,
                           shadow_alpha=205, shadow_blur=11, shadow_dy=4)
        cx += fnt.getlength(seg)
        i = j


# ---------------------------------------------------------------- 基础图形

def rounded(im, r):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, im.size[0] - 1, im.size[1] - 1], radius=r, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def round_rgba(im, r):
    if r <= 0:
        return im
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, im.size[0] - 1, im.size[1] - 1], radius=r, fill=255)
    out = im.copy()
    out.putalpha(mask)
    return out


def fit_cover(im, w, h):
    sc = max(w / im.width, h / im.height)
    im2 = im.resize((max(int(im.width * sc), 1), max(int(im.height * sc), 1)), Image.LANCZOS)
    x, y = (im2.width - w) // 2, (im2.height - h) // 2
    return im2.crop((x, y, x + w, y + h))


def fit_contain(im, w, h):
    sc = min(w / im.width, h / im.height)
    return im.resize((max(int(im.width * sc), 1), max(int(im.height * sc), 1)), Image.LANCZOS)


def is_animated(path):
    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return False


VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi")


def is_video(path):
    """PIL 打不开的视频容器（mp4/webm/…）。这类素材的前景要交给 ffmpeg 叠加。"""
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def first_frame(path, start=0.0):
    """视频素材抽一帧（带缓存），供 PIL 做模糊底 / 封面。start 为起始秒。"""
    key = hashlib.md5(f"{os.path.abspath(path)}|{start:.3f}".encode()).hexdigest()[:16]
    d = os.path.join(tempfile.gettempdir(), "promo_firstframes")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, key + ".png")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        cmd = [FFMPEG, "-y", "-v", "error"]
        if start and start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", path, "-frames:v", "1", out]
        subprocess.run(cmd, check=True)
    return out


def star_points(cx, cy, r_out, r_in, n=5):
    """五角星顶点，尖角朝上。"""
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def draw_star_pill(canvas, right_x, y, stars, fnt):
    """★ 12,345 的金色胶囊，右对齐摆放。返回 (宽, 高)。"""
    d = ImageDraw.Draw(canvas)
    txt = f"{stars:,}"
    tw = d.textlength(txt, font=fnt)
    size = int(getattr(fnt, "size", 30))
    h = size + 28
    w = int(tw + size * 0.95 + 52)
    x = right_x - w
    paste_rrect(canvas, (x, y, x + w, y + h), h // 2,
                (38, 33, 12, 228), (146, 118, 30), 2)
    cx = x + 22 + size * 0.42
    d.polygon(star_points(cx, y + h / 2, size * 0.42, size * 0.168), fill=GOLD)
    draw_text_soft(canvas, (x + 22 + size * 0.95, y + (h - size) / 2 - size * 0.17),
                   txt, fnt, GOLD, shadow_alpha=140, shadow_blur=6, shadow_dy=2)
    return w, h


# ---------------------------------------------------------------- 页面元素

def draw_topbar(canvas, repo, scene, total, W, lay):
    d = ImageDraw.Draw(canvas)
    pad, av = lay["pad"], lay["avatar"]
    ax, ay = pad, lay["topbar_y"]
    d.ellipse([ax, ay, ax + av, ay + av], fill=(35, 44, 55), outline=BORDER, width=2)
    fi = font(F_BOLD, int(av * 0.5))
    initial = repo["name"][0].upper()
    d.text((ax + av / 2 - d.textlength(initial, font=fi) / 2, ay + av / 2 - av * 0.36),
           initial, font=fi, fill=ACCENT)

    fx = ax + av + 24
    name = f"{repo['owner']}/{repo['name']}"
    draw_text_soft(canvas, (fx, ay - 4), name, font(F_BOLD, 36),
                   grad=((244, 249, 253), (188, 203, 218)),
                   shadow_alpha=170, shadow_blur=8, shadow_dy=3)
    d = ImageDraw.Draw(canvas)
    d.text((fx, ay + 46), f"{repo['language']} · {repo['license']}",
           font=font(F_REG, 25), fill=MUTED)

    if lay["portrait"]:
        draw_star_pill(canvas, W - pad, ay - 4, repo["stars"], font(F_BOLD, 30))
        d = ImageDraw.Draw(canvas)
        pg = f"{scene['idx']:02d} / {total:02d}"
        fpg = font(F_REG, 24)
        d.text((W - pad - d.textlength(pg, font=fpg), ay + 54), pg, font=fpg, fill=MUTED)
    else:
        url = repo["html_url"].replace("https://", "")
        fu = font(F_REG, 27)
        d.text((W - pad - d.textlength(url, font=fu), ay + 2), url, font=fu, fill=MUTED)
        cap = repo.get("captured", "")
        if cap:
            note = f"★ 数据截至 {cap[:7].replace('-', '.')}"
            fn = font(F_REG, 23)
            d.text((W - pad - d.textlength(note, font=fn), ay + 44), note,
                   font=fn, fill=(92, 104, 118))

    d.line([(pad, ay + av + 30), (W - pad, ay + av + 30)], fill=(30, 38, 48), width=2)


def draw_name_card(img, repo, cw, ch):
    """无素材时在媒体区内画一张项目名片。"""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=22,
                        fill=(17, 22, 29, 255), outline=(38, 46, 56), width=2)
    mid = ch // 2
    r0, ctop = 78, mid - 218
    d.ellipse([cw // 2 - r0, ctop, cw // 2 + r0, ctop + 2 * r0],
              fill=(24, 46, 33), outline=(46, 120, 70), width=3)
    fbig = font(F_BOLD, 86)
    inch = repo["name"][0].upper()
    d.text((cw // 2 - d.textlength(inch, font=fbig) / 2, ctop + r0 - 58),
           inch, font=fbig, fill=ACCENT)
    fname = font(F_BOLD, 54)
    d.text((cw // 2 - d.textlength(repo["name"], font=fname) / 2, mid + 26),
           repo["name"], font=fname, fill=TEXT)
    furl = font(F_REG, 32)
    url = repo["html_url"].replace("https://", "")
    d.text((cw // 2 - d.textlength(url, font=furl) / 2, mid + 104), url,
           font=furl, fill=MUTED)


def compose_media(asset_path, cw, ch, radius, repo, lay, start=0.0):
    """媒体区：模糊铺底 + 主图（圆角/投影/描边），返回 RGBA。

    静态图：PIL 直接画前景。
    GIF / 视频：只用其中一帧铺模糊底，前景交给 ffmpeg 叠加（保持动感）。
    """
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
    if asset_path and os.path.exists(asset_path):
        src = first_frame(asset_path, start) if is_video(asset_path) else asset_path
        with Image.open(src) as s:
            first = s.convert("RGB")
        back = fit_cover(first, cw, ch).filter(ImageFilter.GaussianBlur(34))
        back = Image.eval(back, lambda v: int(v * 0.42))
        img.paste(back, (0, 0))
        if not is_animated(asset_path) and not is_video(asset_path):
            iw, ih = cw - lay["media_inset"][0], ch - lay["media_inset"][1]
            fg = fit_contain(first, iw, ih)
            fx, fy = (cw - fg.width) // 2, (ch - fg.height) // 2
            sh = Image.new("RGBA", (fg.width + 60, fg.height + 60), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle(
                [30, 36, fg.width + 30, fg.height + 36], radius=18, fill=(0, 0, 0, 165))
            img.paste(sh, (fx - 30, fy - 30), sh)
            shot = rounded(fg, 16)
            img.paste(shot, (fx, fy), shot)
            ImageDraw.Draw(img).rounded_rectangle(
                [fx, fy, fx + fg.width - 1, fy + fg.height - 1],
                radius=16, outline=(58, 66, 78), width=2)
        # 动图前景交给 ffmpeg 叠加（背景已用首帧铺好）
    else:
        draw_name_card(img, repo, cw, ch)
    return round_rgba(img, radius)


def build_slide(scene, repo, total, bg, W, H, base, lay):
    canvas = bg.copy()
    draw_topbar(canvas, repo, scene, total, W, lay)

    asset_rel = scene.get("asset")
    asset_path = os.path.join(base, asset_rel) if asset_rel else None

    mx, my, mw, mh = lay["media_x"], lay["media_y"], lay["media_w"], lay["media_h"]
    media = compose_media(asset_path, mw, mh, lay["media_radius"], repo, lay,
                          start=float(scene.get("asset_start") or 0.0))
    canvas.paste(media, (mx, my), media)

    caption, flags = parse_rich(scene["caption"])
    kicker = scene.get("kicker")
    d = ImageDraw.Draw(canvas)

    # ---- 画面内文字（横屏）：星标 / 页码 / 标签都放进画面
    if lay["in_frame"]:
        # 胶囊自带不透明底色，浅色截图上也能读；只加一层极淡的底部暗角压边
        paste_scrim(canvas, mx, my + mh - 120, mw, 120, alpha1=110, power=2.2)
        rx = mx + mw - 44
        _, sh = draw_star_pill(canvas, rx, my + 40, repo["stars"], font(F_BOLD, 30))
        draw_pill_right(canvas, rx, my + 40 + sh + 14,
                        f"{scene['idx']:02d} / {total:02d}", font(F_BOLD, 26),
                        pad_x=22, pad_y=12, fill=(14, 20, 27, 228),
                        outline=(58, 66, 78), txt_fill=(178, 190, 204), min_h=48)
        if kicker:
            draw_pill(canvas, mx + 44, my + mh - 104, kicker, font(F_BOLD, 28),
                      glow=True)

    # ---- 标签胶囊（竖屏：画面下方）
    if not lay["in_frame"] and kicker:
        fk = font(F_BOLD, 30)
        kw = d.textlength(kicker, font=fk)
        kw, kh = draw_pill(canvas, W // 2 - (kw + 56) / 2, lay["kicker_y"],
                           kicker, fk, glow=True)

    # ---- 字幕
    fc = font(F_BOLD, lay["cap_size"])
    lines = wrap_ranges(d, caption, fc, W - lay["cap_x"] * 2 - (0 if lay["cap_align"] == "center" else 0))
    y = lay["cap_y"]
    if lay["cap_bar"]:
        paste_rrect(canvas, (lay["cap_x"] - 26, y + 8, lay["cap_x"] - 14, y + lay["cap_lh"] - 12),
                    4, (46, 160, 67, 255))
    for (a, b) in lines:
        lw = d.textlength(caption[a:b], font=fc)
        x = (W - lw) / 2 if lay["cap_align"] == "center" else lay["cap_x"]
        draw_rich_line(canvas, x, y, caption, flags, a, b, fc)
        y += lay["cap_lh"]

    # ---- 底部进度轨（横屏）
    if lay["rail_y"]:
        x0, x1, ry = lay["pad"], W - lay["pad"], lay["rail_y"]
        paste_rrect(canvas, (x0, ry, x1, ry + 6), 3, (34, 42, 52, 255))
        paste_rrect(canvas, (x0, ry, x0 + (x1 - x0) * scene["idx"] / total, ry + 6),
                    3, (46, 160, 67, 255))
    return canvas


def render_clip(slide, duration, out, W, H, FPS, asset_path, lay, start=0.0):
    frames = max(int(round(duration * FPS)), 1)
    step = 0.035 / frames
    zoom = (f"zoompan=z='min(zoom+{step:.8f},1.035)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}")
    fade = f"fade=t=in:st=0:d=0.22,fade=t=out:st={max(duration-0.28,0.01):.3f}:d=0.28"

    if asset_path and (is_animated(asset_path) or is_video(asset_path)):
        # 与静图口径一致：只留 media_inset 的边距，不再额外避让（横屏字幕在画面外）
        aw = lay["media_w"] - lay["media_inset"][0]
        ah = lay["media_h"] - lay["media_inset"][1]
        oy = f"{lay['media_y']}+({lay['media_h']}-h)/2"
        fc = (f"[1:v]scale={aw}:{ah}:force_original_aspect_ratio=decrease,"
              f"fps={FPS},setpts=PTS-STARTPTS,"
              f"pad=iw+6:ih+6:3:3:color=0x39414d[g];"
              f"[0:v][g]overlay=x=(W-w)/2:y={oy}[b];"
              f"[b]{fade},format=yuv420p[v]")
        cmd = [FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", slide]
        if start and start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-stream_loop", "-1", "-i", asset_path,
                "-filter_complex", fc, "-map", "[v]", "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-r", str(FPS), "-pix_fmt", "yuv420p", out]
    else:
        cmd = [FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", slide,
               "-t", f"{duration:.3f}", "-vf", f"{zoom},{fade},format=yuv420p",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-r", str(FPS), "-pix_fmt", "yuv420p", out]
    subprocess.run(cmd, check=True)


def write_covers(base, repo):
    """生成 B站 / YouTube 风格封面（设计说明见 make_cover.py）。

    横竖两版都出：横屏版另存一份 YouTube 规范的 1280x720 JPG。
    文案取 cover.json，缺省时从 script.json / repo.json 自动推导。
    """
    import make_cover as mk
    spec = mk.load_spec(base, repo, {})
    outs = []
    outs += mk.render_wide(base, spec, repo,
                           os.path.join(base, "cover_16x9.png"),
                           os.path.join(base, "cover_youtube_1280x720.jpg"))
    outs += mk.render_tall(base, spec, repo, os.path.join(base, "cover_9x16.png"))
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--ratio", choices=["9:16", "16:9"], default="9:16",
                    help="9:16 -> 1080x1920（竖屏，默认）；16:9 -> 1920x1080（横屏）")
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--out", default=None)
    ap.add_argument("--audio", default="auto",
                    help="auto=优先 soundtrack.wav，其次 narration.wav；也可指定文件")
    ap.add_argument("--force", action="store_true", help="忽略片段缓存，强制重渲画面")
    args = ap.parse_args()

    W, H = (1920, 1080) if args.ratio == "16:9" else (1080, 1920)
    if args.w:
        W = args.w
    if args.h:
        H = args.h
    FPS = args.fps

    base = os.path.abspath(args.dir)
    repo = json.load(open(os.path.join(base, "repo.json"), encoding="utf-8"))
    scenes = json.load(open(os.path.join(base, "script.json"), encoding="utf-8"))
    tl = json.load(open(os.path.join(base, "timeline.json"), encoding="utf-8"))
    durs = tl["durations"]

    for dname in ("slides", "clips"):
        os.makedirs(os.path.join(base, dname), exist_ok=True)

    lay = layout(W, H)
    bg = make_bg(W, H)
    total = len(scenes)

    print(f"[1/3] 渲染 {total} 张画面（{W}x{H}）", flush=True)
    clips = []
    for s in scenes:
        i, D = s["idx"], durs[s["idx"] - 1]
        slide_p = os.path.join(base, "slides", f"s{i:02d}.png")
        asset_p = os.path.join(base, s["asset"]) if s.get("asset") else None
        out = os.path.join(base, "clips", f"v{i:02d}_{W}x{H}.mp4")

        # 缓存判定：片段比 script.json / repo.json / 素材都新即可复用（画面改了才需重渲）
        srcs = [os.path.join(base, "script.json"), os.path.join(base, "repo.json")]
        if asset_p:
            srcs.append(asset_p)
        cached = False
        if not args.force and os.path.exists(out):
            t = os.path.getmtime(out)
            if all(os.path.exists(p) and t > os.path.getmtime(p) for p in srcs):
                cached = abs(probe_duration(out) - D) < 0.15
        # 幻灯片总是重出（供人工抽查/封面复用），但不影响缓存判定
        build_slide(s, repo, total, bg, W, H, base, lay).save(slide_p)
        if cached:
            print(f"  scene {i}: {D:5.2f}s  复用片段", flush=True)
        else:
            render_clip(slide_p, D, out, W, H, FPS, asset_p, lay,
                        start=float(s.get("asset_start") or 0.0))
            kind = ("视频" if asset_p and is_video(asset_p)
                    else "动图" if asset_p and is_animated(asset_p)
                    else "静图" if asset_p else "CTA")
            print(f"  scene {i}: {D:5.2f}s  {kind}", flush=True)
        clips.append(out)

    print("[2/3] 拼接 + 混流", flush=True)
    lst = os.path.join(base, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in clips:
            f.write(f"file '{p}'\n")
    silent = os.path.join(base, "_silent.mp4")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", silent], check=True)

    final = args.out or os.path.join(base, "final.mp4")
    if not final.lower().endswith(".mp4"):
        final += ".mp4"

    # 音轨选择：有混音轨（配音+BGM+音效）就优先用
    nar_wav = os.path.join(base, "narration.wav")
    st_wav = os.path.join(base, "soundtrack.wav")
    if args.audio != "auto":
        audio = args.audio
    elif os.path.exists(st_wav):
        audio = st_wav
    else:
        audio = nar_wav
    if not os.path.exists(audio):
        raise SystemExit(f"找不到音轨: {audio}")
    if audio == st_wav and os.path.exists(nar_wav) \
            and os.path.getmtime(nar_wav) > os.path.getmtime(st_wav) + 1:
        print("  警告: narration.wav 比 soundtrack.wav 新，混音轨可能已过期"
              "（改过脚本请重跑 make_soundtrack.py）", flush=True)
    print(f"  音轨: {os.path.basename(audio)}", flush=True)

    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", silent,
                    "-i", audio,
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    "-shortest", final], check=True)

    print("[3/3] 生成封面", flush=True)
    covers = write_covers(base, repo)

    d = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", final],
                             capture_output=True, text=True).stdout.strip())
    print(f"\n成片: {final}")
    print(f"  {W}x{H} {FPS}fps  {d:.2f}s ({d/60:.2f} min)  "
          f"{os.path.getsize(final)/1024/1024:.2f}MB")
    for c in covers:
        print(f"封面: {c}")


if __name__ == "__main__":
    main()
