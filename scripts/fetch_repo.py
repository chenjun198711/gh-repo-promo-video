# -*- coding: utf-8 -*-
"""fetch_repo.py — 抓取 GitHub 仓库元数据 + README 中的演示素材。

依赖: gh (已登录)、httpx、Pillow

用法:
    python fetch_repo.py <owner/repo> --out <目录> [--max-assets 10] [--max-mb 20]
    python fetch_repo.py https://github.com/owner/repo --out <目录>

产出:
    <out>/repo.json          元数据 + 素材清单（后续写脚本/渲染都用它）
    <out>/assets/NN_xxx.gif  下载的演示图 / GIF / 视频
    <out>/og.png             GitHub 社交卡片图（封面备选）
"""
import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from datetime import date

import httpx

# ---------------------------------------------------------------- 正则

MD_IMG = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["\'][^"\']*["\'])?\s*\)')
HTML_MEDIA = re.compile(r'<(?:img|source|video)[^>]+src=["\']([^"\']+)["\']', re.I)
CODE_FENCE = re.compile(r'```.*?```', re.S)

# 徽章 / 访客计数 / 赞助按钮等，不是演示素材，按域名和路径特征剔除
BADGE_PAT = re.compile(
    r'('
    r'shields\.io|badge\.fury|badgen\.net|codecov|coveralls|travis-ci|circleci|'
    r'appveyor|sonarcloud|snyk\.io|opencollective|badgesize|codacy|gitter|'
    r'deepwiki|repology|wakatime\.com|hits\.seeyoufarm|komarev\.com|'
    r'star-history|readme-typing-svg|profile-counter|visitor-badge|starchart|'
    r'herokuapp\.com/badge|docker\.com/.*/build|nodei\.co|install-count|'
    r'/actions/workflows/.*\.svg|img\.shields|api\.github\.com|contrib\.rocks|'
    r'forthebadge|gitpod\.io|vercel\.com/button|producthunt\.com'
    r')', re.I)

GOOD_EXT = (".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp",
            ".mp4", ".webm", ".mov")
VIDEO_EXT = (".mp4", ".webm", ".mov")


# ---------------------------------------------------------------- gh helper

def gh(args, raw=False):
    """调用 gh api，返回 stdout。raw=True 时直接返回文件内容。"""
    cmd = ["gh", "api"] + args
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} 失败: {r.stderr.strip()[:400]}")
    return r.stdout


def gh_json(path):
    return json.loads(gh([path]))


def parse_repo(spec):
    """接受 owner/repo 或完整 URL，返回 owner/repo。"""
    spec = spec.strip().rstrip("/")
    m = re.search(r'github\.com[/:]([^/]+)/([^/#?\s]+)', spec)
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    parts = spec.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    raise SystemExit(f"无法解析仓库标识: {spec}")


# ---------------------------------------------------------------- 素材提取

def normalize_url(src, full_name, branch):
    """把 README 里的相对路径 / 各种写法统一成可下载的绝对 URL。"""
    src = src.strip().strip('<>').strip()
    if not src:
        return None
    if src.startswith("//"):
        return "https:" + src
    if src.startswith(("http://", "https://")):
        return src
    # 仓库内相对路径
    src = src.split("#")[0].split("?")[0]
    if src.startswith("/"):
        path = src.lstrip("/")
    else:
        path = posixpath.normpath(src).lstrip("./")
    while path.startswith("../"):
        path = path[3:]
    if not path:
        return None
    return f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}"


def extract_media(readme, full_name, branch):
    """从 README 里提取候选素材 URL（保序、去重）。"""
    body = CODE_FENCE.sub("", readme)  # 代码块里的示例链接不算
    urls = []
    for rx in (MD_IMG, HTML_MEDIA):
        for m in rx.finditer(body):
            u = normalize_url(m.group(1), full_name, branch)
            if not u:
                continue
            if u in urls:
                continue
            urls.append(u)
    return urls


def has_good_ext(url):
    tail = url.split("?")[0].lower()
    return tail.endswith(GOOD_EXT)


def download(url, dest, max_mb):
    """下载单个素材，超过体积上限返回 None。"""
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=90) as r:
            if r.status_code != 200:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_mb * 1024 * 1024:
                    return None
        if len(buf) < 2048:      # 太小，基本是占位图
            return None
        with open(dest, "wb") as f:
            f.write(bytes(buf))
        return len(buf)
    except Exception:
        return None


def probe_size(path):
    """读图片尺寸；视频/PIL 打不开的返回 (0, 0)。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/repo 或 GitHub URL")
    ap.add_argument("--out", required=True, help="项目工作目录")
    ap.add_argument("--max-assets", type=int, default=10)
    ap.add_argument("--max-mb", type=float, default=20.0, help="单个素材体积上限 MB")
    ap.add_argument("--min-px", type=int, default=320, help="图片最小边长，过滤图标")
    args = ap.parse_args()

    full_name = parse_repo(args.repo)
    out = os.path.abspath(args.out)
    assets_dir = os.path.join(out, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    print(f"[1/5] 拉取元数据 {full_name}", flush=True)
    meta = gh_json(f"repos/{full_name}")
    branch = meta.get("default_branch") or "main"

    lic = (meta.get("license") or {}).get("spdx_id") or "未声明"
    info = {
        "full_name": meta["full_name"],
        "owner": meta["owner"]["login"],
        "name": meta["name"],
        "description": (meta.get("description") or "").strip(),
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "language": meta.get("language") or "",
        "license": lic,
        "topics": meta.get("topics") or [],
        "homepage": meta.get("homepage") or "",
        "default_branch": branch,
        "pushed_at": (meta.get("pushed_at") or "")[:10],
        "created_at": (meta.get("created_at") or "")[:10],
        "html_url": meta.get("html_url", ""),
        "avatar_url": meta["owner"].get("avatar_url", ""),
        "open_issues": meta.get("open_issues_count", 0),
        "captured": date.today().isoformat(),
    }

    print("[2/5] 拉取 README", flush=True)
    readme = ""
    try:
        readme = gh([f"repos/{full_name}/readme"], raw=True)
    except Exception as e:
        print(f"  README 获取失败（继续）: {e}", flush=True)
    # 去掉 HTML 标签噪声，截断给写稿用
    clean = re.sub(r'<[^>]+>', ' ', readme)
    clean = re.sub(r'[ \t]+', ' ', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    info["readme_excerpt"] = clean[:6000]

    info["readme_images_raw"] = len(extract_media(readme, full_name, branch))

    print("[3/5] 下载素材", flush=True)
    candidates = [u for u in extract_media(readme, full_name, branch)
                  if not BADGE_PAT.search(u) and has_good_ext(u)]
    candidates = candidates[:args.max_assets * 3]   # 多下一些，后面再筛

    def grab(idx_url):
        i, url = idx_url
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        tmp = os.path.join(assets_dir, f"_tmp{i:03d}{ext}")   # 每个候选独立文件名
        size = download(url, tmp, args.max_mb)
        if not size:
            return None
        if ext in VIDEO_EXT:
            w = h = 0
        else:
            w, h = probe_size(tmp)
            if w and h and max(w, h) < args.min_px:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return None
        return {"src_url": url, "tmp": tmp, "bytes": size,
                "w": w, "h": h, "ext": ext}

    from concurrent.futures import ThreadPoolExecutor
    kept = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(grab, enumerate(candidates)):
            if res:
                kept.append(res)

    # 排序：GIF 优先（能动的演示），其次按面积降序
    kept.sort(key=lambda k: (0 if k["ext"] == ".gif" else 1, -(k["w"] * k["h"])))
    kept = kept[:args.max_assets]

    final_assets = []
    for i, k in enumerate(kept, 1):
        kind = "video" if k["ext"] in VIDEO_EXT else ("gif" if k["ext"] == ".gif" else "image")
        tag = {"gif": "demo", "video": "demo", "image": "shot"}[kind]
        name = f"{i:02d}_{tag}{k['ext']}"
        dest = os.path.join(assets_dir, name)
        if not os.path.exists(k["tmp"]):
            continue
        try:
            os.replace(k["tmp"], dest)
        except OSError as e:
            print(f"  跳过 {name}: {e}", flush=True)
            continue
        final_assets.append({
            "file": f"assets/{name}",
            "kind": kind,
            "w": k["w"], "h": k["h"],
            "bytes": k["bytes"],
            "src_url": k["src_url"],
        })
        print(f"  {name}  {k['w']}x{k['h']}  {k['bytes']//1024}KB", flush=True)
    # 清掉没用上的临时文件
    for f in os.listdir(assets_dir):
        if f.startswith("_tmp"):
            os.remove(os.path.join(assets_dir, f))

    info["assets"] = final_assets

    print("[4/5] 抓社交卡片图", flush=True)
    og = os.path.join(out, "og.png")
    if download(f"https://opengraph.githubassets.com/1/{full_name}", og, 5):
        info["og_image"] = "og.png"
        print("  og.png 已保存", flush=True)
    else:
        info["og_image"] = None
        print("  跳过", flush=True)

    print("[5/5] 写 repo.json", flush=True)
    with open(os.path.join(out, "repo.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)

    print(f"\n完成: {out}")
    print(f"  {info['full_name']}  ★{info['stars']}  {info['language']}  {info['license']}")
    print(f"  素材 {len(final_assets)} 个（GIF: {sum(1 for a in final_assets if a['kind']=='gif')}）")
    print(f"  README 中图片总数: {info['readme_images_raw']}，可用: {len(final_assets)}")


if __name__ == "__main__":
    main()
