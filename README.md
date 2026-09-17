# gh-repo-promo-video

把一份现成的 GitHub 仓库（README + 截图 + Star 数）自动翻译成 **1 分钟推广短视频**，
并同时产出 **B站 / YouTube 风格的高点击封面**。

同一份 `script.json` 可以出 **9:16 竖屏** 和 **16:9 横屏** 两版，画面片段缓存按画幅分开存，互不覆盖。

## 它为什么能流水线化

这类视频不是"创作"，而是**"翻译"**——素材全部现成（README 里的截图、录屏、Star 数），
缺的只是一套固定流程。所以从抓素材到成片+封面，能一条命令跑完。

## 全流程（五个命令）

```bash
# 1) 抓素材（唯一没法省的人工环节，其余都能自动）
python scripts/fetch_repo.py <owner/repo> --out <WORKDIR>

# 2) 写脚本 → <WORKDIR>/script.json（格式见 references/script_template.md，这步由模型写）

# 2.5) 写封面文案 → <WORKDIR>/cover.json（可选但强烈建议）

# 3) 配音 + 时间轴（带缓存，改稿只重合成改动段）
python scripts/make_audio.py --dir <WORKDIR> --target 58

# 4) 背景音乐 + 转场音效，混成一轨（程序化合成，无素材文件）
python scripts/make_soundtrack.py --dir <WORKDIR> --dump-stems

# 5) 渲染成片 + 封面（有 soundtrack.wav 就自动用它）
python scripts/make_video.py --dir <WORKDIR> --out <输出.mp4>              # 竖屏 1080x1920
python scripts/make_video.py --dir <WORKDIR> --ratio 16:9 --out <输出.mp4> # 横屏 1920x1080

# 5b) 只想重出封面（改完文案不用重渲视频）
python scripts/make_cover.py --dir <WORKDIR> --ratio both
```

## 脚本一览

| 脚本 | 作用 |
|---|---|
| `fetch_repo.py` | 抓仓库元数据（Star/Fork/License/语言）+ README，下载其中的图片与 gif 素材 |
| `make_audio.py` | 火山引擎 TTS（seed-tts-2.0）逐段配音，多音色可选，输出整轨 + 时间轴 |
| `make_soundtrack.py` | numpy 程序化合成 BGM（84BPM)+ 转场音效 + 人声侧链闪避 |
| `make_video.py` | 逐场渲染 + 烧字幕 + 混音，支持静态图 / GIF / **mp4 录屏素材** |
| `make_cover.py` | 封面生成，三尺寸：1920×1080、1280×720（YouTube 规范）、1080×1920 |
| `compose_wide.py` | 把竖版手机截图合成为横屏可用的宽幅图（横屏项目必需） |
| `verify_soundtrack.py` | 校验音轨长度与画面总时长是否一致 |
| `trending.py` | 从 GitHub Trending 自动挑候选选题 |
| `ocr_dir.ps1` | 批量 OCR 素材目录，辅助判断截图内容 |

## 环境依赖

- **Python**：`httpx`、`numpy`、`Pillow`（另可选 `faster-whisper` 做字幕校验）
- **ffmpeg**：需在 PATH 中，或装在 winget 默认目录（脚本会自动找）
- **字体**：默认用微软雅黑（Windows）
- **火山引擎 TTS Key**（第四步必需）

### 配置 TTS Key

源码里**不留 Key**。按顺序取，先命中先用：

1. 环境变量 `VOLC_TTS_KEY`
2. `scripts/.volc_key` 文件（一行纯文本，已写进 `.gitignore`，只存在于本机）

```bash
# 方式一
export VOLC_TTS_KEY=你的Key

# 方式二（Windows）
echo 你的Key > scripts/.volc_key
```

两者都没有时脚本会直接报错并提示写法。

## 目录结构

```
.
├── SKILL.md                     # 完整说明书：字数预算、音色表、封面设计规则、踩坑记录
├── references/
│   └── script_template.md       # script.json 的格式与六段式叙事模板
└── scripts/                     # 上面那张表里的所有脚本
```

## 使用建议

- **默认片长 60±3s**，文案 290 字左右、8 场；要 90s 约 340 字 / 9 场，120s 约 480 字 / 11 场。
- **语速**：推广片推荐「播报腔 + 1.3x」，即 `--speed-rate 30`。
- ⚠️ **换音色或改语速必须加 `--force`**，否则缓存会静默复用旧音色。
- ⚠️ 输出文件名用 **ASCII**，中文文件名会让 ffmpeg 产出 0 字节文件；交付前再复制成中文名。

详细规则、坑位与调试经验都在 **`SKILL.md`**。
