# -*- coding: utf-8 -*-
"""trending.py — 抓取 GitHub Trending，输出候选选题表。

用法:
    python trending.py [--since daily|weekly|monthly] [--limit 25]

输出: 每行一个候选，含 仓库 / Star / 增量 / 语言 / 一句话描述 + 可演示性标记。
      ★ 标记表示 README 里大概率有界面截图或演示动图（更适合直接做成片）。
"""
import argparse
import html
import re
import sys

import httpx

ART = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
H2 = re.compile(r'<h2[^>]*>.*?href="/([^"]+)"', re.S)
DESC = re.compile(r'<p class="col-9 color-fg-muted my-1 tmp-pr-4">\s*(.*?)\s*</p>', re.S)
LANG = re.compile(r'itemprop="programmingLanguage">([^<]+)</span>')
STARS = re.compile(r'/stargazers"[^>]*>\s*(?:<svg.*?</svg>)?\s*([\d,]+)', re.S)
TODAY = re.compile(r'([\d,]+)\s*stars?\s*today', re.I)

# 有这些词说明是能演示的应用类项目，而不是纯库
DEMO_HINTS = re.compile(
    r'\b(app|application|gui|desktop|dashboard|ui|cli|tool|toolkit|platform|'
    r'studio|editor|viewer|player|manager|assistant|agent|chat|server|browser|'
    r'client|extension|plugin|generator|builder|scanner|analyzer|converter)\b',
    re.I)
# 这些通常是纯库/框架，没有可拍画面
LIB_HINTS = re.compile(
    r'\b(library|framework|sdk|binding|bindings|wrapper|parser|protocol|spec|'
    r'kernel module|header-only|template)\b', re.I)


def fetch(since="daily"):
    url = f"https://github.com/trending?since={since}"
    r = httpx.get(url, follow_redirects=True, timeout=60,
                  headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def parse(page):
    out = []
    for a in ART.findall(page):
        m = H2.search(a)
        if not m:
            continue
        repo = m.group(1).strip("/")
        dm = DESC.search(a)
        desc = html.unescape(re.sub(r"<[^>]+>", "", dm.group(1))).strip() if dm else ""
        lm = LANG.search(a)
        sm = STARS.search(a)
        tm = TODAY.search(a)
        out.append({
            "repo": repo,
            "stars": int(sm.group(1).replace(",", "")) if sm else 0,
            "stars_today": int(tm.group(1).replace(",", "")) if tm else 0,
            "language": lm.group(1) if lm else "",
            "desc": desc,
        })
    return out


def score(item):
    """粗略判断适不适合做视频：描述里像应用，且不是纯库。"""
    d = item["desc"]
    if LIB_HINTS.search(d) and not re.search(r'\b(app|gui|desktop|dashboard)\b', d, re.I):
        return 0
    return 1 if DEMO_HINTS.search(d) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="daily",
                    choices=["daily", "weekly", "monthly"])
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    items = parse(fetch(args.since))[:args.limit]
    if not items:
        print("没抓到内容（GitHub 页面结构可能又变了，检查 ART/H2 正则）")
        return

    print(f"GitHub Trending ({args.since}) — {len(items)} 个候选")
    print("=" * 100)
    for i, it in enumerate(items, 1):
        mark = "★" if score(it) else " "
        print(f"{i:2d}.{mark} {it['repo']:<42} ★{it['stars']:>7,}  +{it['stars_today']:>4,}/d  {it['language']}")
        print(f"      {it['desc'][:130]}")
    print("=" * 100)
    print("★ = 大概率有可演示界面，适合直接做成片；其余多为库/框架，缺少画面素材")
    print(f"  按可演示性排序的推荐: " + ", ".join(
        it["repo"] for it in sorted(items, key=score, reverse=True)[:5]))


if __name__ == "__main__":
    sys.exit(main())
