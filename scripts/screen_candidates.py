# -*- coding: utf-8 -*-
"""screen_candidates.py — 给 trending.py 抓到的候选仓库做「能不能做成片」的核验。

做选题时最耗时间的不是抓榜单，而是逐个确认：Star 是否达标、License 是否允许转载、
README 里到底有没有能直接用的截图/动图。这个脚本把这一步一次做完。

用法:
    # 1) 直接传仓库
    python screen_candidates.py jamiepine/voicebox Tencent/WeKnora

    # 2) 从 trending.py 的输出里挑（管道自动解析 owner/repo）
    python trending.py --since daily --limit 25 | python screen_candidates.py

    # 3) 只看结果表 / 要机器可读输出
    python screen_candidates.py --from-file picks.txt --quiet
    python screen_candidates.py --from-file picks.txt --json out.json

输出列:
    仓库 | Star | 语言 | License | README 素材(数量/类型) | 可演示判定 | 说明

判定逻辑（仅供参考，最终仍要人判断一句话痛点是否成立）:
    demo=strong  有截图/动图/视频 且 描述像应用类      → 可直接开工
    demo=medium  有素材但是图片可能是 logo/架构图      → 需自己补录屏
    demo=weak    无素材 或 描述像纯库/框架            → 不建议

License 提示:
    GitHub 的 spdx_id 在 LICENSE 带自定义版权头时会返回 NOASSERTION，
    此时脚本会拉 LICENSE 原文关键词重新定性（AGPL/GPL/MIT/Apache/BSL…）。
    fair-code / Sustainable Use License / BSL 属「源码可见但非标准开源」，会标 ⚠。
"""
import argparse
import base64
import json
import re
import subprocess
import sys

# ---------- README 素材检测 ----------
# 注意：现代 README 大量使用 HTML <img src>，只匹配 Markdown ![]() 会大面积漏检
MD_IMG = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["\'][^"\']*["\'])?\s*\)')
HTML_MEDIA = re.compile(r'<(?:img|source|video)[^>]+src=["\']([^"\']+)["\']', re.I)
MEDIA_EXT = re.compile(r'\.(png|jpe?g|gif|webp|svg|mp4|webm|mov)(?:[?#]|$)', re.I)

# 徽章/装饰性图片，不是演示素材
BAD_PAT = re.compile(
    r'shields\.io|badge|badgen|travis|codecov|circleci|fossa|repobeats|contrib\.rocks|'
    r'star-history|contributors-img|opencollective|twitter|x\.com|discord|'
    r'hostinger|deploy\.svg|/actions/workflows/|api\.github\.com|paypal|buymeacoffee',
    re.I)
# 纯 logo / 图标，通常不适合当画面主体
LOGO_PAT = re.compile(r'logo|icon|favicon|qrcode|qr-code|banner', re.I)
SCREEN_PAT = re.compile(r'screenshot|screen|shot|preview|overview|demo|hero|chat|ui|'
                        r'dashboard|console|panel|result|output|benchmark', re.I)

LIB_HINTS = re.compile(
    r'\b(library|framework|sdk|binding|bindings|wrapper|parser|protocol|spec)\b', re.I)
DEMO_HINTS = re.compile(
    r'\b(app|application|gui|desktop|dashboard|ui|cli|tool|toolkit|platform|studio|'
    r'editor|viewer|player|manager|assistant|agent|chat|server|browser|client|'
    r'extension|plugin|generator|builder|scanner|analyzer|converter|launcher)\b', re.I)

FAMOUS_PLATFORM = {"n8n-io/n8n"}   # 超大成熟平台，短视频增量信息少，默认降级


def sh(cmd, timeout=45):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.stdout if p.returncode == 0 else None


def api(path):
    out = sh(["gh", "api", path])
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def read_license(repo, meta):
    spdx = ((meta.get("license") or {}) or {}).get("spdx_id")
    if spdx and spdx != "NOASSERTION":
        return spdx, ""
    d = api(f"repos/{repo}/license")
    if not d or "content" not in d:
        return spdx or "UNKNOWN", ""
    txt = base64.b64decode(d["content"]).decode("utf-8", "replace")
    head = txt[:3000]
    up = head.upper()
    for key, name in (("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL (自定义头)"),
                      ("GNU LESSER GENERAL PUBLIC", "LGPL (自定义头)"),
                      ("GNU GENERAL PUBLIC LICENSE", "GPL (自定义头)"),
                      ("APACHE LICENSE", "Apache-2.0 (自定义头)"),
                      ("MIT LICENSE", "MIT (自定义头)"),
                      ("BSD", "BSD (自定义头)"),
                      ("SUSTAINABLE USE", "Sustainable Use (fair-code) ⚠非标准开源"),
                      ("BUSINESS SOURCE LICENSE", "BSL ⚠非标准开源"),
                      ("SERVER SIDE PUBLIC LICENSE", "SSPL ⚠"),
                      ("ELASTIC LICENSE", "Elastic ⚠非标准开源")):
        if key in up:
            return name, txt[:200].replace("\n", " ")
    return (spdx or "UNKNOWN"), txt[:200].replace("\n", " ")


def scan_assets(repo, readme):
    """返回 (素材清单, 演示向素材数, 是否有动图/视频)"""
    found = MD_IMG.findall(readme) + HTML_MEDIA.findall(readme)
    good, bad = [], []
    for u in found:
        lu = u.lower()
        if BAD_PAT.search(lu) or not MEDIA_EXT.search(lu):
            bad.append(u)
            continue
        good.append(u)

    # 去重，保持顺序
    seen, uniq = set(), []
    for u in good:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    # 相对路径要校验文件真实存在
    verified = []
    for u in uniq:
        if u.startswith(("http://", "https://")):
            verified.append((u, "remote"))
            continue
        p = u.lstrip("./").split("?")[0].split("#")[0]
        if api(f"repos/{repo}/contents/{p}") is not None:
            verified.append((p, "exists"))
        else:
            bad.append(u + " (路径不存在)")

    screens = [(u, k) for u, k in verified if SCREEN_PAT.search(u.lower())]
    motion = [u for u, _ in verified if re.search(r'\.(gif|mp4|webm|mov)(?:[?#]|$)', u, re.I)]
    return verified, screens, motion, bad


def verdict(meta, verified, screens, motion):
    desc = meta.get("description") or ""
    pure_lib = bool(LIB_HINTS.search(desc)) and not re.search(
        r'\b(app|application|gui|desktop|dashboard|cli|platform)\b', desc, re.I)
    if pure_lib:
        return "weak", "描述像纯库/框架，无独立画面载体"
    if motion:
        return "strong", f"README 直接带 {len(motion)} 个动图/视频，画面素材最省事"
    if screens:
        return "strong", f"检出 {len(screens)} 张演示向截图（screenshot/demo/ui 等命名）"
    if verified:
        return "medium", f"只有 {len(verified)} 张图片且偏 logo/banner/架构图，需自己补录屏"
    return "weak", "README 没有任何可用图片素材，画面要全造"


def screen(repo, extra_note=""):
    meta = api(f"repos/{repo}")
    if not meta:
        return {"repo": repo, "error": "gh api 拉取失败（私有/改名/限流）"}
    lic, lic_raw = read_license(repo, meta)
    readme = ""
    d = api(f"repos/{repo}/readme")
    if d and "content" in d:
        try:
            readme = base64.b64decode(d["content"]).decode("utf-8", "replace")
        except Exception:
            readme = ""
    verified, screens, motion, bad = scan_assets(repo, readme)
    v, why = verdict(meta, verified, screens, motion)
    if repo in FAMOUS_PLATFORM:
        v = "medium"
        why += "；已是超大成熟平台，短视频增量信息有限"
    if "⚠" in lic:
        why += "；许可非标准开源，转载/二次创作需谨慎"
    return {
        "repo": repo,
        "stars": meta.get("stargazers_count"),
        "language": meta.get("language"),
        "license": lic,
        "archived": meta.get("archived"),
        "homepage": meta.get("homepage"),
        "desc": (meta.get("description") or "")[:120],
        "verdict": v,
        "why": why + ("；" + extra_note if extra_note else ""),
        "n_verified_assets": len(verified),
        "n_screens": len(screens),
        "motion": motion[:5],
        "screens": [u for u, _ in screens][:6],
        "discarded": len(bad),
        "readme_len": len(readme),
    }


def parse_repos(argv, from_file):
    repos, seen = [], set()

    def add(r):
        r = r.strip().strip("/").strip(".,")
        if re.fullmatch(r'[\w.-]+/[\w.-]+', r) and r not in seen:
            seen.add(r)
            repos.append(r)

    for a in argv:
        add(a)
    if from_file:
        with open(from_file, encoding="utf-8-sig") as f:
            for line in f:
                m = re.search(r'([\w.-]+/[\w.-]+)', line)
                if m:
                    add(m.group(1))
    if not sys.stdin.isatty():
        for line in sys.stdin:
            m = re.search(r'([\w.-]+/[\w.-]+)', line)
            if m:
                add(m.group(1))
    return repos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="*")
    ap.add_argument("--from-file", help="每行含 owner/repo 的文件")
    ap.add_argument("--json", help="把结果写到指定 json 文件")
    ap.add_argument("--min-stars", type=int, default=1000)
    ap.add_argument("--quiet", action="store_true", help="只输出表，不打进度")
    args = ap.parse_args()

    repos = parse_repos(args.repos, args.from_file)
    if not repos:
        print("没有解析到 owner/repo。用法: python screen_candidates.py owner/repo ... "
              "或  python trending.py | python screen_candidates.py")
        return 2

    results = []
    for i, r in enumerate(repos, 1):
        if not args.quiet:
            print(f"[{i}/{len(repos)}] {r} ...", file=sys.stderr, flush=True)
        results.append(screen(r))

    order = {"strong": 0, "medium": 1, "weak": 2, None: 3}
    results.sort(key=lambda x: (order.get(x.get("verdict"), 3),
                                -(x.get("stars") or 0)))

    print(f"\n{'仓库':<40}{'Star':>9}  {'License':<26}{'素材':>5}{'演示':>8}  说明")
    print("=" * 132)
    for x in results:
        if "error" in x:
            print(f"{x['repo']:<40}{'—':>9}  {'—':<26}{'—':>5}{'ERR':>8}  {x['error']}")
            continue
        flag = "⚠低星" if (x["stars"] or 0) < args.min_stars else ""
        print(f"{x['repo']:<40}{x['stars']:>9,}  {x['license'][:25]:<26}"
              f"{x['n_verified_assets']:>5}{x['verdict']:>8}  {x['why']} {flag}")
        for u in x["screens"][:3]:
            print(f"{'':<40}{'':>9}  {'':<26}{'':>5}{'':>8}  · {u}")
    print("=" * 132)
    print(f"共 {len(results)} 个；strong={sum(1 for x in results if x.get('verdict')=='strong')} "
          f"medium={sum(1 for x in results if x.get('verdict')=='medium')} "
          f"weak={sum(1 for x in results if x.get('verdict')=='weak')}")
    print("提示：素材数量与许可只是初筛，『一句话能不能说清痛点』仍需人工判断。")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"JSON 已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
