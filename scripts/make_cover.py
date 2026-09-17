# -*- coding: utf-8 -*-
"""make_cover.py — B站 / YouTube 风格封面（缩略图）生成器。

这不是"截一帧当封面"，而是一张专门设计的缩略图。设计原则：

1. **3 秒可读** —— 它在信息流里只有 ~200px 宽，所以只放三层信息：
   一句反差大字（≤2 行） + 一个主体（软件界面） + 一个行动指引。
2. **强对比** —— 暗底 + 高饱和强调色，大字带黑色描边，缩到最小也不糊。
3. **主体"浮"起来** —— 截图包一层窗口外框（红黄绿三点）+ 投影 + 品牌色光晕，
   一眼看出是真实软件而不是一张图。
4. **关键数字/反差词用琥珀色高亮** —— 在文案里用 `**...**` 标记（与 caption 同一套语法）。

用法：
    python make_cover.py --dir <WORKDIR> [--ratio 16:9|9:16|both]
                         [--headline "..."] [--sub "..."] [--badge "..."] [--cta "..."]
                         [--asset assets/xx.png] [--start 38] [--accent 46,160,67]

文案取值优先级：命令行参数 > `<WORKDIR>/cover.json` > 从 script.json / repo.json 自动推导。

产出：
    <WORKDIR>/cover_16x9.png                1920x1080  通用（B站 / 官网嵌入）
    <WORKDIR>/cover_youtube_1280x720.jpg    1280x720   YouTube 规范尺寸
    <WORKDIR>/cover_9x16.png                1080x1920  竖屏平台
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_video import (F_BOLD, F_REG, first_frame, fit_contain,  # noqa: E402
                        font, is_video, parse_rich)

# ---------------------------------------------------------------- 配色
# 比视频正片更"跳"：封面要在信息流里抢注意力，允许高饱和。
C_BG_TOP = (8, 11, 17)
C_BG_BOT = (20, 27, 38)
ACCENT = (46, 160, 67)          # GitHub 绿（品牌锚点）
AMBER = (255, 199, 61)          # 关键数字 / 反差词
WHITE = (247, 251, 254)
GRAD_HEAD = ((255, 255, 255), (198, 213, 230))   # 大字竖向渐变
MUTED = (158, 172, 188)

STAR_GOLD = (255, 208, 74)

NO_START = "，。、；：？！）】》」』…·%,.;:?!)]}”’"


# ---------------------------------------------------------------- 字体
def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(F_REG, size)


def _has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


# ---------------------------------------------------------------- 背景
def _radial(W, H, cx, cy, rx, ry, color, alpha):
    """椭圆径向光斑（小图算完放大，比逐像素快一个量级）。"""
    sw = 360
    sh = max(int(round(sw * H / W)), 8)
    small = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    px = small.load()
    for y in range(sh):
        dy = (y / sh - cy) / ry
        for x in range(sw):
            dx = (x / sw - cx) / rx
            t = 1.0 - (dx * dx + dy * dy)
            if t > 0:
                px[x, y] = color + (int(alpha * t * t),)
    return small.resize((W, H), Image.BICUBIC)


def _dot_grid(img, step=58, alpha=15):
    """极淡的点阵，给纯渐变底一点"技术感"的质感。"""
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(step, img.height, step):
        for x in range(step, img.width, step):
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(130, 160, 195, alpha))


def make_bg(W, H, accent):
    bg = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(C_BG_TOP[i] + (C_BG_BOT[i] - C_BG_TOP[i]) * t)
                          for i in range(3)) + (255,))
    # 斜向光束（左侧暗区与右侧主体区分开，制造纵深）
    beam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(beam).polygon(
        [(W * 0.52, -H * 0.2), (W * 1.02, -H * 0.2), (W * 0.78, H * 1.2), (W * 0.30, H * 1.2)],
        fill=accent + (30,))
    bg = Image.alpha_composite(bg, beam.filter(ImageFilter.GaussianBlur(W * 0.045)))
    # 主体背后的品牌色光晕
    bg = Image.alpha_composite(bg, _radial(W, H, 0.74, 0.44, 0.52, 0.72, accent, 100))
    # 左上角冷光，避免左半屏死黑
    bg = Image.alpha_composite(bg, _radial(W, H, 0.05, -0.06, 0.45, 0.52, (58, 104, 232), 62))
    _dot_grid(bg)
    return bg


# ---------------------------------------------------------------- 描边大字
def _seg_boxes(fnt, seg, sw, blur):
    pad = int(fnt.size * 0.42 + sw + blur)
    w = int(fnt.getlength(seg)) + 2 * pad + 6
    h = int(fnt.size * 1.6) + 2 * pad + 6
    return pad, max(w, 8), max(h, 8)


def draw_cover_text(canvas, x, y, segments, fnt, *, stroke=None, glow=None,
                    glow_alpha=130, glow_blur=None, shadow_alpha=165,
                    shadow_dy=None, shadow_blur=None, grad=GRAD_HEAD):
    """带描边 / 外发光 / 投影的大字，逐段（高亮段可换色）。

    segments: [(文本, 颜色 or None)]，None 表示用竖向渐变填充。
    """
    size = fnt.size
    sw = int(stroke if stroke is not None else max(size * 0.055, 3))
    gb = int(glow_blur if glow_blur is not None else size * 0.20)
    sb = int(shadow_blur if shadow_blur is not None else size * 0.11)
    sdy = int(shadow_dy if shadow_dy is not None else size * 0.045)
    cx = x
    for seg, color in segments:
        if not seg:
            continue
        pad, bw, bh = _seg_boxes(fnt, seg, sw, max(gb, sb))
        ox = oy = pad
        px, py = int(round(cx - ox)), int(round(y - oy))

        def _mask(width_stroke):
            m = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(m).text(
                (ox, oy), seg, font=fnt, fill=255,
                stroke_width=(sw if width_stroke else 0), stroke_fill=255)
            return m

        # 外发光（高亮段用琥珀色光晕，正文段给一点冷光）
        if glow:
            gl = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            gl.paste(tuple(glow) + (glow_alpha,), (0, 0), _mask(True))
            gl = gl.filter(ImageFilter.GaussianBlur(gb))
            canvas.paste(gl, (px, py), gl)
        # 投影
        if shadow_alpha:
            sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            sh.paste((0, 0, 0, shadow_alpha), (0, 0), _mask(True))
            sh = sh.filter(ImageFilter.GaussianBlur(sb))
            canvas.paste(sh, (px, py + sdy), sh)
        # 黑色描边（撑住结构，缩到 200px 宽也不糊）
        st = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        st.paste((4, 7, 11, 235), (0, 0), _mask(True))
        canvas.paste(st, (px, py), st)
        # 填充：高亮段纯色，正文段竖向渐变
        fm = _mask(False)
        if color:
            fill = Image.new("RGBA", (bw, bh), tuple(color) + (255,))
        else:
            g = Image.new("RGBA", (bw, bh))
            gd = ImageDraw.Draw(g)
            for yy in range(bh):
                t = yy / (bh - 1)
                gd.line([(0, yy), (bw, yy)],
                        fill=(tuple(int(grad[0][i] + (grad[1][i] - grad[0][i]) * t)
                                    for i in range(3)) + (255,)))
            fill = g
        canvas.paste(fill, (px, py), fm)
        cx += fnt.getlength(seg)
    return cx


def _wrap_plain(text, fnt, max_w):
    """按像素宽度折行（含 \\n 与中文禁则），返回 [(a, b)]。"""
    lines, start, n = [], 0, len(text)
    while start < n:
        end, w = start, 0.0
        while end < n:
            if text[end] == "\n":
                break
            adv = fnt.getlength(text[end])
            if w + adv > max_w and end > start:
                break
            w += adv
            end += 1
        while end < n and end - start > 1 and text[end] in NO_START and text[end - 1] not in " \u3000":
            end -= 1
        lines.append((start, end))
        start = end
        if start < n and text[start] == "\n":
            start += 1
    if len(lines) >= 2:
        s, e = lines[-1]
        ps, pe = lines[-2]
        if e - s <= 1 and pe - ps > 3:
            lines[-2] = (ps, pe - 1)
            lines[-1] = (pe - 1, e)
    return lines


def _is_word_char(c):
    """ASCII 字母/数字才算"词内字符"——中文可以在任意字之间断，英文单词不行。"""
    return c.isascii() and c.isalnum()


def _splits_word(text, lines):
    """折行是否把一个英文单词从中间劈开（如 TrendRadar → TrendRad / ar）。"""
    for (_a, b) in lines[:-1]:
        if 0 < b < len(text) and _is_word_char(text[b - 1]) and _is_word_char(text[b]):
            return True
    return False


def plan_headline(text, fnt_path, max_w, cap, floor, max_lines=2, step=3):
    """选字号 + 折行；标题长度不可控，所以先算好再画（版式才居得中）。

    行数达标还不够——如果这一版折行把英文单词劈开了，就继续缩小字号再试，
    直到它能整词换行（纯中文标题不受影响，中文本来就允许任意断行）。
    """
    size = cap
    while size > floor:
        f = _font(fnt_path, size)
        lines = _wrap_plain(text, f, max_w)
        if len(lines) <= max_lines and (len(lines) < 2 or not _splits_word(text, lines)):
            return f, lines
        size -= step
    f = _font(fnt_path, floor)
    return f, _wrap_plain(text, f, max_w)


def draw_headline(canvas, x, y, f, lines, plain, flags, accent,
                  align="left", box_w=0, line_gap=1.16):
    """按 plan 画封面大字，返回底部 y。高亮段（**...**）用琥珀色 + 光晕。"""
    cy = y
    for (a, b) in lines:
        segs, i = [], a
        while i < b:
            hl = flags[i]
            j = i
            while j < b and flags[j] == hl:
                j += 1
            segs.append((plain[i:j], AMBER if hl else None))
            i = j
        if align == "center":
            total = sum(f.getlength(s) for s, _ in segs)
            sx = x + (box_w - total) / 2
        else:
            sx = x
        for s, col in segs:
            draw_cover_text(canvas, sx, cy, [(s, col)], f,
                            glow=(col or accent), glow_alpha=140 if col else 70,
                            shadow_alpha=170)
            sx += f.getlength(s)
        cy += f.size * line_gap
    return cy


def block_metrics(plain, max_w, cap, floor):
    """文字块（大字 + 强调条 + 副标题）的字号、折行与总高。"""
    f, lines = plan_headline(plain, F_BOLD, max_w, cap, floor, max_lines=2)
    return f, lines, len(lines) * f.size * 1.16 + 22 + 9 + 46 + 46


def draw_text_block(canvas, x, y, max_w, plain, flags, sub, accent, align="left",
                    plan=None):
    """大字 + 强调条 + 副标题，返回整体高度（用于垂直居中）。"""
    f, lines, h = plan if plan else block_metrics(plain, max_w, 172, 88)
    fsub = _font(F_REG, 38)
    cy = draw_headline(canvas, x, y, f, lines, plain, flags, accent, align=align,
                       box_w=max_w)
    cy += 22
    d = ImageDraw.Draw(canvas)
    if align == "center":
        d.rounded_rectangle([x + (max_w - 96) / 2, cy, x + (max_w + 96) / 2, cy + 9],
                            radius=5, fill=accent + (255,))
    else:
        d.rounded_rectangle([x, cy, x + 96, cy + 9], radius=5, fill=accent + (255,))
    cy += 46
    tw = fsub.getlength(sub)
    sx = x + (max_w - tw) / 2 if align == "center" else x
    draw_cover_text(canvas, sx, cy, [(sub, (214, 226, 240))], fsub,
                    stroke=0, glow=None, shadow_alpha=150, shadow_dy=3)
    return h


# ---------------------------------------------------------------- 小元素
def _pill_size(canvas, text, fnt, pad_x, pad_y):
    d = ImageDraw.Draw(canvas)
    return int(d.textlength(text, font=fnt) + 2 * pad_x), int(fnt.size + 2 * pad_y)


def _pill_at(canvas, x, y, text, fnt, *, fill, txt, outline=None, pad_x=30, pad_y=15,
             radius=None, glow=None, glow_alpha=120, align="left"):
    """胶囊标签；返回 (宽, 高)。align='right' 时 x 表示右边缘。"""
    w, h = _pill_size(canvas, text, fnt, pad_x, pad_y)
    r = radius if radius is not None else h // 2
    x0 = x - w if align == "right" else x
    if glow:
        gl = Image.new("RGBA", (w + 120, h + 120), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle(
            [60, 60, w + 60, h + 60], radius=r + 8, fill=tuple(glow) + (glow_alpha,))
        gl = gl.filter(ImageFilter.GaussianBlur(26))
        canvas.paste(gl, (int(x0) - 60, int(y) - 60), gl)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle([0, 0, w - 1, h - 1], radius=r,
                                         fill=tuple(fill) + (255,),
                                         outline=(tuple(outline) + (255,)) if outline else None,
                                         width=2)
    canvas.paste(ov, (int(x0), int(y)), ov)
    draw_cover_text(canvas, x0 + (w - fnt.getlength(text)) / 2,
                    y + (h - fnt.size) / 2 - fnt.size * 0.16,
                    [(text, txt)], fnt, stroke=0, glow=None, shadow_alpha=120,
                    shadow_blur=max(int(fnt.size * 0.1), 3), shadow_dy=2)
    return w, h


def star_badge(canvas, right_x, y, stars, fnt, label="★"):
    """金色星标胶囊（右对齐），返回 (宽, 高)。"""
    return _pill_at(canvas, right_x, y, f"{label} {stars:,}", fnt,
                    fill=(46, 34, 8), txt=STAR_GOLD, outline=(146, 108, 20),
                    pad_x=30, pad_y=14, glow=(255, 190, 40), glow_alpha=70,
                    align="right")


# ---------------------------------------------------------------- 主体卡片
def _name_card(repo, w, h):
    """没有可用素材时的兜底主体：一张项目名片（比留白强得多）。"""
    im = Image.new("RGB", (w, h), (17, 22, 29))
    d = ImageDraw.Draw(im)
    cx, cy = w // 2, h // 2
    r0 = max(int(min(w, h) * 0.17), 40)
    top = cy - r0 - int(r0 * 0.55)
    d.ellipse([cx - r0, top, cx + r0, top + 2 * r0], fill=(24, 46, 33),
              outline=(60, 150, 90), width=4)
    f1 = _font(F_BOLD, int(r0 * 1.05))
    ch = (repo.get("name") or "?")[:1].upper()
    d.text((cx - f1.getlength(ch) / 2, top + r0 - f1.size * 0.62), ch, font=f1, fill=ACCENT)
    f2 = _font(F_BOLD, max(int(min(w, h) * 0.10), 26))
    nm = repo.get("name", "")
    d.text((cx - f2.getlength(nm) / 2, top + 2 * r0 + int(r0 * 0.5)), nm, font=f2, fill=WHITE)
    f3 = _font(F_REG, max(int(min(w, h) * 0.055), 18))
    url = repo.get("html_url", "").replace("https://", "")
    d.text((cx - f3.getlength(url) / 2, top + 2 * r0 + int(r0 * 0.5) + f2.size + 26),
           url, font=f3, fill=MUTED)
    return im


def build_card(base, spec, box_w, box_h, title, repo=None, frame=True, radius=22):
    """把素材做成一张"浮起的窗口卡片"（外框 + 三点 + 标题），返回 RGBA。

    素材缺失时退化成项目名片，保证右半边不会是空的。
    """
    ap = spec.get("asset")
    path = os.path.join(base, ap) if ap and not os.path.isabs(ap) else ap
    head = 46 if frame else 0
    if path and os.path.exists(path):
        src = first_frame(path, float(spec.get("asset_start") or 0.0)) \
            if is_video(path) else path
        with Image.open(src) as im:
            first = im.convert("RGB")
    elif repo:
        first = _name_card(repo, max(box_w, 640), max(box_h - head, 400))
    else:
        return None
    inner = fit_contain(first, box_w, box_h - head)
    cw, ch = inner.width, inner.height + head
    card = Image.new("RGBA", (cw, ch), (24, 30, 39, 255))
    if frame:
        hd = Image.new("RGBA", (cw, head), (30, 37, 47, 255))
        dd = ImageDraw.Draw(hd)
        dd.line([(0, head - 1), (cw, head - 1)], fill=(52, 62, 76, 255), width=2)
        for i, col in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
            cx = 26 + i * 26
            dd.ellipse([cx - 7, head // 2 - 7, cx + 7, head // 2 + 7], fill=col)
        tf = _font(F_BOLD, 22)
        # 标题过长时截断，避免压到右上角
        t = str(title or "")
        while t and tf.getlength(t) > cw - 170:
            t = t[:-1]
        dd.text((118, head // 2 - tf.size * 0.68), t, font=tf, fill=(122, 137, 154))
        card.paste(hd, (0, 0), hd)
    card.paste(inner, (0, head))
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(card, (0, 0), mask)
    return out


def paste_card(canvas, card, x, y, accent, glow=True, radius=22):
    """带光晕 + 投影 + 描边地把卡片贴到画布上。"""
    w, h = card.size
    if glow:
        gl = Image.new("RGBA", (w + 180, h + 180), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle([90, 90, w + 90, h + 90], radius=radius + 10,
                                             fill=tuple(accent) + (95,))
        g = gl.filter(ImageFilter.GaussianBlur(48))
        canvas.paste(g, (int(x) - 90, int(y) - 90), g)
    sh = Image.new("RGBA", (w + 160, h + 160), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([80, 80, w + 80, h + 80], radius=radius + 6,
                                         fill=(0, 0, 0, 190))
    s = sh.filter(ImageFilter.GaussianBlur(34))
    canvas.paste(s, (int(x) - 80 + 10, int(y) - 80 + 26), s)
    canvas.paste(card, (int(x), int(y)), card)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius,
                                         outline=(112, 130, 156, 170), width=2)
    canvas.paste(ov, (int(x), int(y)), ov)


def put_card(canvas, card, bx, by, bw, bh, accent, radius=22):
    """把卡片在给定框内居中放置，返回卡片左上角坐标。"""
    if not card:
        return None
    x = bx + (bw - card.width) // 2
    y = by + (bh - card.height) // 2
    paste_card(canvas, card, x, y, accent, radius=radius)
    return x, y


def draw_callouts(canvas, callouts, card_pos, card_size, fnt, radius=22):
    """在卡片上叠加标注胶囊（fractional 相对坐标），模仿"圈重点"的封面习惯。"""
    if not callouts or not card_pos:
        return
    cx0, cy0 = card_pos
    cw, ch = card_size
    for co in callouts:
        text = co.get("text")
        if not text:
            continue
        fx, fy = float(co.get("x", 0.5)), float(co.get("y", 0.5))
        d = ImageDraw.Draw(canvas)
        w = int(d.textlength(text, font=fnt) + 60)
        h = int(fnt.size + 26)
        x = cx0 + cw * fx - w / 2
        y = cy0 + ch * fy
        x = min(max(x, cx0 + 12), cx0 + cw - w - 12)
        _pill_at(canvas, x, y, text, fnt, fill=(12, 17, 24), txt=AMBER,
                 outline=(198, 150, 30), pad_x=30, pad_y=13,
                 glow=(255, 190, 40), glow_alpha=80)


# ---------------------------------------------------------------- 文案推导
def _trim(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip("，,。.、·-— ") + "…"


def load_spec(base, repo, cli):
    spec = {}
    cp = os.path.join(base, "cover.json")
    if os.path.exists(cp):
        try:
            spec = json.load(open(cp, encoding="utf-8")) or {}
        except Exception:
            spec = {}

    # 非 GitHub 项目的推广片（VPS / SaaS / 软件测评）没有 repo.json：
    # 允许在 cover.json 里写 name / owner / stars / language / license / description / html_url，
    # 取值后再把 repo 补全，后面所有版式代码无需再判断来源。
    for k in ("name", "owner", "stars", "language", "license",
              "description", "html_url"):
        if repo.get(k) in (None, "") and spec.get(k) not in (None, ""):
            repo[k] = spec.pop(k)
    repo.setdefault("name", "Promo")
    repo.setdefault("owner", "")
    repo.setdefault("stars", 0)

    scenes = []
    sp = os.path.join(base, "script.json")
    if os.path.exists(sp):
        try:
            scenes = json.load(open(sp, encoding="utf-8")) or []
        except Exception:
            scenes = []

    # 自动推导：钩子场的大字当封面标题，第一场的素材当主体
    if not spec.get("headline") and scenes:
        hook = scenes[0]
        spec["headline"] = hook.get("caption") or repo["name"]
        spec.setdefault("asset", hook.get("asset"))
        if hook.get("asset_start"):
            spec.setdefault("asset_start", hook["asset_start"])
    # 既没有 cover.json 也没有 script.json 时兜底，避免后面 spec["headline"] 直接 KeyError
    if not spec.get("headline"):
        spec["headline"] = repo["name"]

    desc = repo.get("description") or ""
    if not spec.get("sub"):
        spec["sub"] = _trim(desc, 30) if _has_cjk(desc) else \
            f"{repo.get('language', '')} · {repo.get('license', '')} · 开源".strip(" ·")
    spec.setdefault("badge", "开源精选")
    spec.setdefault("cta", f"GitHub 搜 {repo['name']}")
    spec.setdefault("rate", "一键三连")
    spec.setdefault("callouts", [])

    for k, v in (cli or {}).items():
        if v not in (None, ""):
            spec[k] = v
    return spec


# ---------------------------------------------------------------- 版式
def render_wide(base, spec, repo, out_png, out_jpg=None):
    W, H = 1920, 1080
    accent = tuple(spec.get("accent") or ACCENT)
    canvas = make_bg(W, H, accent)
    pad_x = 104

    # ---- 主体卡片（右侧）
    bx, by, bw, bh = 992, 140, 900, 700
    card = build_card(base, spec, bw, bh, spec.get("title") or repo["name"], repo)
    pos = put_card(canvas, card, bx, by, bw, bh, accent)
    draw_callouts(canvas, spec.get("callouts"), pos, card.size if card else (0, 0),
                  _font(F_BOLD, 30))

    # ---- 顶部：徽标 + 仓库名（左）· 星标（右）
    fb = _font(F_BOLD, 28)
    w_badge, h_badge = _pill_at(canvas, pad_x, 82, spec["badge"], fb,
                                fill=accent, txt=(6, 24, 12), glow=accent, glow_alpha=110)
    fname = _font(F_BOLD, 38)
    name_segs = ([("{}".format(repo["owner"]) + "/", (200, 214, 230))]
                 if repo.get("owner") else []) + [(repo["name"], None)]
    draw_cover_text(canvas, pad_x + w_badge + 22, 82 + h_badge * 0.5 - fname.size * 0.68,
                    name_segs, fname, stroke=0, glow=None, shadow_alpha=140)
    meta = spec.get("meta")
    if meta is None:
        meta = f"{repo.get('language', '')} · {repo.get('license', '')} · 开源".strip(" ·")
    if meta:
        fm = _font(F_REG, 27)
        ImageDraw.Draw(canvas).text((pad_x + w_badge + 22, 82 + h_badge + 16),
                                    meta, font=fm, fill=(146, 162, 180))
    # 右上角：有 Star 数就出星标，否则（非 GitHub 项目）出 eyebrow 胶囊
    if repo.get("stars"):
        star_badge(canvas, W - pad_x, 78, repo["stars"], _font(F_BOLD, 32))
    elif spec.get("eyebrow"):
        _pill_at(canvas, W - pad_x, 78, spec["eyebrow"], _font(F_BOLD, 30),
                 fill=(46, 34, 8), txt=AMBER, outline=(146, 108, 20), pad_x=30, pad_y=15,
                 glow=(255, 190, 40), glow_alpha=70, align="right")

    # ---- 大字 + 副标题 + 行动指引（左列）
    plain, flags = parse_rich(spec["headline"])
    max_w = 856
    plan = block_metrics(plain, max_w, 172, 88)
    cta_top = H - 96 - 92
    top = max(228.0, (228 + cta_top) / 2 - plan[2] / 2)
    draw_text_block(canvas, pad_x, top, max_w, plain, flags, spec["sub"], accent,
                    plan=plan)
    fc = _font(F_BOLD, 40)
    _pill_at(canvas, pad_x, cta_top, spec["cta"] + "  →", fc, fill=accent,
             txt=(250, 255, 252), pad_x=46, pad_y=17, radius=46,
             glow=accent, glow_alpha=110)

    canvas.convert("RGB").save(out_png)
    outs = [out_png]
    if out_jpg:
        canvas.convert("RGB").resize((1280, 720), Image.LANCZOS).save(
            out_jpg, "JPEG", quality=90, optimize=True)
        outs.append(out_jpg)
    return outs


def render_tall(base, spec, repo, out_png):
    W, H = 1080, 1920
    accent = tuple(spec.get("accent") or ACCENT)
    canvas = make_bg(W, H, accent)
    d = ImageDraw.Draw(canvas)

    fb = _font(F_BOLD, 30)
    bw_, bh_ = _pill_size(canvas, spec["badge"], fb, 30, 15)
    _pill_at(canvas, (W - bw_) / 2, 132, spec["badge"], fb, fill=accent, txt=(6, 24, 12),
             glow=accent, glow_alpha=110)
    if repo.get("stars"):
        star_badge(canvas, W - 64, 132, repo["stars"], _font(F_BOLD, 28))
    elif spec.get("eyebrow"):
        _pill_at(canvas, W - 64, 132, spec["eyebrow"], _font(F_BOLD, 28),
                 fill=(46, 34, 8), txt=AMBER, outline=(146, 108, 20), pad_x=28, pad_y=14,
                 glow=(255, 190, 40), glow_alpha=70, align="right")
    fname = _font(F_BOLD, 40)
    nm = f"{repo['owner']}/{repo['name']}" if repo.get("owner") else repo["name"]
    draw_cover_text(canvas, (W - fname.getlength(nm)) / 2, 132 + bh_ + 30,
                    [(nm, (206, 219, 234))], fname, stroke=0, glow=None, shadow_alpha=130)

    plain, flags = parse_rich(spec["headline"])
    max_w = 900
    plan = block_metrics(plain, max_w, 128, 72)
    draw_text_block(canvas, 90, 400, max_w, plain, flags, spec["sub"], accent,
                    align="center", plan=plan)

    card = build_card(base, spec, 940, 660, spec.get("title") or repo["name"], repo)
    pos = put_card(canvas, card, 70, 1010, 940, 660, accent)
    draw_callouts(canvas, spec.get("callouts"), pos, card.size if card else (0, 0),
                  _font(F_BOLD, 28))

    fc = _font(F_BOLD, 42)
    cta = spec["cta"] + "  →"
    cw_ = _pill_size(canvas, cta, fc, 48, 18)[0]
    _pill_at(canvas, (W - cw_) / 2, 1730, cta, fc, fill=accent, txt=(250, 255, 252),
             pad_x=48, pad_y=18, radius=49, glow=accent, glow_alpha=110)
    canvas.convert("RGB").save(out_png)
    return [out_png]


def main():
    ap = argparse.ArgumentParser(description="生成 B站/YouTube 风格封面")
    ap.add_argument("--dir", required=True,
                    help="项目目录（含 cover.json；有 repo.json / script.json 会自动读取）")
    ap.add_argument("--ratio", choices=["16:9", "9:16", "both"], default="16:9")
    ap.add_argument("--headline", default=None, help="封面大字，用 **...** 标琥珀色高亮")
    ap.add_argument("--sub", default=None, help="副标题一行")
    ap.add_argument("--badge", default=None, help="左上角徽标文字（默认 开源精选）")
    ap.add_argument("--cta", default=None, help="行动指引胶囊文字")
    ap.add_argument("--asset", default=None, help="主体素材路径（相对项目目录）")
    ap.add_argument("--start", type=float, default=None, help="素材为视频时的起始秒")
    ap.add_argument("--accent", default=None, help="强调色，如 46,160,67")
    ap.add_argument("--out", default=None, help="覆盖输出文件名（png）")
    args = ap.parse_args()

    base = os.path.abspath(args.dir)
    # repo.json 是可选的：非 GitHub 项目（VPS / SaaS / 软件测评）的字段写在 cover.json 里
    rp = os.path.join(base, "repo.json")
    repo = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
    cli = {"headline": args.headline, "sub": args.sub, "badge": args.badge,
           "cta": args.cta, "asset": args.asset, "asset_start": args.start}
    if args.accent:
        cli["accent"] = tuple(int(v) for v in args.accent.split(","))
    spec = load_spec(base, repo, cli)

    outs = []
    if args.ratio in ("16:9", "both"):
        outs += render_wide(base, spec, repo,
                            os.path.join(base, args.out or "cover_16x9.png"),
                            None if args.out else os.path.join(base, "cover_youtube_1280x720.jpg"))
    if args.ratio in ("9:16", "both"):
        outs += render_tall(base, spec, repo, os.path.join(base, "cover_9x16.png"))
    for p in outs:
        print(f"封面: {p}", flush=True)


if __name__ == "__main__":
    main()
