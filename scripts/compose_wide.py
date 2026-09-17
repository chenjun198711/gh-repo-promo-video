#!/usr/bin/env python
"""把竖版手机截图合成为横屏可用的宽幅图（整屏 + 局部放大）。

为什么需要它：README 里常见的手机截图是 750px 宽的竖图，直接丢进 16:9 视频的
素材区会被 contain 压成一条窄柱（只占画面宽度 ~28%），周围全是模糊背景，观感很弱。
本脚本把「整屏」放左边、把「关键区域放大」放右边，合成 1600x1000（1.6:1），
在 1920x1080 的素材区里能铺满，且放大版承担了「让人看清」的职责。

用法:
    python compose_wide.py --src shots/push.png --out assets/push_wide.png
    python compose_wide.py --src shots/push.png --out assets/push_wide.png --zoom 120,740
    python compose_wide.py --src shots/push.png --out assets/push_wide.png --batch  # 批量处理一个目录

参数:
    --zoom  y0,y1   放大区域在原图中的纵向范围（默认取中间 60%），横向始终取满宽
    --accent        放大卡片的强调色，形如 52,176,108
    --size          输出尺寸，默认 1600x1000
"""
import argparse
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ACCENT = (52, 176, 108)


def rounded(im, r):
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=r, fill=255)
    out = im.convert("RGBA")
    out.putalpha(m)
    return out


def compose(src_path, out_path, zoom=None, accent=ACCENT, size=(1600, 1000)):
    W, H = size
    with Image.open(src_path) as im:
        src = im.convert("RGB")
    sw, sh = src.size
    if sw >= sh * 1.2:
        print(f"  跳过（本身已是横图 {sw}x{sh}）: {src_path}")
        return None

    # 背景：整图铺满并模糊压暗，避免左右两侧发空
    bg = src.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(46))
    bg = Image.eval(bg, lambda v: int(v * 0.30))
    canvas = bg.convert("RGBA")

    # 左：整屏（高 88% 画布）
    ph = int(H * 0.88)
    pw = int(sw * ph / sh)
    px, py = int(W * 0.05), (H - ph) // 2
    shadow = Image.new("RGBA", (pw + 60, ph + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([20, 26, pw + 40, ph + 46], radius=30,
                                             fill=(0, 0, 0, 150))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)), (px - 20, py - 26))
    canvas.alpha_composite(rounded(src.resize((pw, ph), Image.LANCZOS), 26), (px, py))
    ImageDraw.Draw(canvas).rounded_rectangle([px, py, px + pw - 1, py + ph - 1], radius=26,
                                             outline=(74, 86, 102, 255), width=3)

    # 右：局部放大，带强调色描边
    if zoom:
        zy0, zy1 = max(int(zoom[0]), 0), min(int(zoom[1]), sh)
    else:
        zy0, zy1 = int(sh * 0.20), int(sh * 0.80)
    if zy1 - zy0 < 80:
        raise SystemExit("--zoom 纵向范围太小")
    crop = src.crop((0, zy0, sw, zy1))
    box_w, box_h = int(W * 0.475), int(H * 0.62)
    sc = min(box_w / crop.width, box_h / crop.height)
    zw, zh = max(int(crop.width * sc), 1), max(int(crop.height * sc), 1)
    qx, qy = int(W * 0.52), (H - zh) // 2
    glow = Image.new("RGBA", (zw + 90, zh + 90), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([45, 45, zw + 45, zh + 45], radius=24,
                                           fill=tuple(accent) + (110,))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28)), (qx - 45, qy - 45))
    canvas.alpha_composite(rounded(crop.resize((zw, zh), Image.LANCZOS), 20), (qx, qy))
    ImageDraw.Draw(canvas).rounded_rectangle([qx, qy, qx + zw - 1, qy + zh - 1], radius=20,
                                             outline=tuple(accent) + (255,), width=4)

    canvas.convert("RGB").save(out_path)
    print(f"  合成 {os.path.basename(out_path)}  {W}x{H}  整屏 {(pw, ph)}  放大 {(zw, zh)}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="竖版手机截图 → 横屏宽幅合成图")
    ap.add_argument("--src", required=True, help="竖版截图；--batch 时传目录")
    ap.add_argument("--out", required=True, help="输出文件；--batch 时传目录")
    ap.add_argument("--zoom", default=None, help="放大区域 y0,y1（默认中间 60%%）")
    ap.add_argument("--accent", default=None, help="强调色 52,176,108")
    ap.add_argument("--size", default="1600x1000", help="输出尺寸 WxH")
    ap.add_argument("--batch", action="store_true", help="把 --src 目录下所有竖图批量合成到 --out 目录")
    args = ap.parse_args()

    accent = tuple(int(v) for v in args.accent.split(",")) if args.accent else ACCENT
    size = tuple(int(v) for v in args.size.lower().split("x"))
    zoom = tuple(int(v) for v in args.zoom.split(",")) if args.zoom else None

    if args.batch:
        os.makedirs(args.out, exist_ok=True)
        files = sorted(f for f in glob.glob(os.path.join(args.src, "*"))
                       if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
        done = 0
        for f in files:
            stem = os.path.splitext(os.path.basename(f))[0]
            if compose(f, os.path.join(args.out, stem + "_w.png"), zoom, accent, size):
                done += 1
        print(f"批量完成: {done}/{len(files)} 张")
        if not done:
            sys.exit("没有可合成的竖图")
    else:
        compose(args.src, args.out, zoom, accent, size)


if __name__ == "__main__":
    main()
