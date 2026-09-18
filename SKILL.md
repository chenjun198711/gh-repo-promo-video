---
name: gh-repo-promo-video
description: 把 GitHub 开源项目做成 1 分钟推广短视频（竖屏 9:16 或横屏 16:9，字幕烧进画面，带背景音乐与转场音效），并自动生成 B站/YouTube 风格的高点击封面（横竖两版 + YouTube 规范尺寸）。一条指令跑完：抓仓库元数据与 README 里的演示素材 → 写六段式脚本 → 火山引擎 TTS 配音 → 程序化合成 BGM/音效并做人声闪避 → 逐场渲染 → 输出成片 + 封面。当用户要求「给这个 GitHub 项目/仓库做推广视频」「每天认识一款黑科技软件」「开源项目介绍短视频」「把 repo 做成短视频」「给视频加背景音乐/音效」「要个吸引人的封面/缩略图/YouTube 封面/B站封面」「改成 16:9 / 横屏 / B站版本」，或指定某个 GitHub 地址要做视频时使用。也支持从 GitHub Trending 自动挑候选选题。**同样适用于非 GitHub 项目**（VPS 选购、SaaS、软件测评等）：只要给一段录屏/截图，写好 cover.json 就能出横竖两版封面（repo.json 可省略）。
agent_created: true
---

# GitHub 项目 → 推广短视频（9:16 竖屏 / 16:9 横屏）

把一份现成的 GitHub 仓库（README + 截图 + Star 数）翻译成 1 分钟推广短片。
**这类视频能流水线化，是因为它不是创作而是翻译**——素材全部现成，缺的只是"翻译"流程。
同一份 script.json + 配音可以同时出竖屏与横屏两版，画面片段缓存按画幅分开存，互不覆盖。
成片之外**必定附一套 B站/YouTube 风格封面**（见第七节）——封面决定点击率，不是附属品。

## 全流程（五个命令）

```bash
PY=<venv python>

# 1) 抓素材（唯一没法省的人工环节，其余都能自动）
python scripts/fetch_repo.py <owner/repo> --out <WORKDIR>
#    抓完先看尺寸：超长截图要裁、竖版手机截图要 compose_wide.py 合成为横屏宽幅（见第二节末）

# 2) 写脚本 → <WORKDIR>/script.json（格式见 references/script_template.md，这步由模型写）

# 2.5) 写封面文案 → <WORKDIR>/cover.json（第七节，可选但强烈建议）

# 3) 配音 + 时间轴（带缓存，改稿只重合成改动段）
python scripts/make_audio.py --dir <WORKDIR> --target 58

# 4) 背景音乐 + 转场音效，混成一轨（程序化合成，无素材文件）
python scripts/make_soundtrack.py --dir <WORKDIR> --dump-stems

# 5) 渲染成片 + 封面（有 soundtrack.wav 就自动用它，否则退回纯配音）
python scripts/make_video.py --dir <WORKDIR> --out <输出.mp4>                 # 竖屏 1080x1920
python scripts/make_video.py --dir <WORKDIR> --ratio 16:9 --out <输出.mp4>    # 横屏 1920x1080

# 5b) 只想重出封面（改完文案不用重渲视频）
python scripts/make_cover.py --dir <WORKDIR> --ratio both
```

`--ratio` 支持 `9:16`（默认）与 `16:9`；也可用 `--w/--h` 精确指定。画面片段缓存按画幅分开存
（`clips/vNN_宽x高.mp4`），所以竖屏横屏两版可以并存、互不覆盖，切画幅不需要重渲另一版。

`--target` 是期望总时长（秒）。脚本会在超长时提示，按提示删减最长的那 2~3 段文案重跑即可。

**迭代友好**：配音按文件缓存（改哪段重配哪段）、画面片段按 mtime 缓存（只调音乐时画面不重渲），
所以「改一段文案 → 出新片」通常十几秒。画面版式改了要加 `--force` 才会重渲片段。

## 一、选题

```bash
python scripts/trending.py --since daily --limit 25
```
输出带 `★` 标记的候选表，`★` = 描述里像能演示的应用（有界面可拍），没标记的多为纯库/框架，**没有画面素材，别选**。

判断标准是「**能不能演示**」，不是「技术是否先进」：
- ✅ 有界面或终端输出、一句话能说清痛点、Star 1k+（社会认同）、License 允许转载
- ❌ 纯库 / 纯框架 / 需要专业环境才能跑起来的项目
- ⚠️ **合规红线**：涉盗版（如课程/影视下载器）、绕过付费墙、抓取隐私数据的项目不要推广；License 为 AGPL/GPL 时避免暗示可闭源商用

### 选题核验：`screen_candidates.py`（别手写临时脚本）

`trending.py` 只给描述层面的初筛，Star / License / README 素材要逐个核验。**这一步用现成脚本，一次吃完**：

```bash
# 管道：直接从榜单输出里挑
python scripts/trending.py --since daily --limit 25 | python scripts/screen_candidates.py
# 或指定仓库 / 从文件读
python scripts/screen_candidates.py jamiepine/voicebox Tencent/WeKnora --json picks.json
```

输出每行：`仓库 | Star | License | 素材数 | strong/medium/weak | 说明`，按可演示性排序。
- `strong` 有动图/视频，或检出 screenshot/demo/ui 等演示向截图 → 可直接开工
- `medium` 只有 logo/banner/架构图 → 画面要自己补录屏
- `weak` 零素材或描述像纯库 → 别选

两个容易踩的坑，脚本已处理：
1. **现代 README 大量用 HTML `<img src>`，只匹配 Markdown `![]()` 会大面积漏检**（17 个仓库几乎全判成"无素材"）。脚本同时匹配 MD / `<img>` / `<video>`。
2. **GitHub 的 `license.spdx_id` 会是 `NOASSERTION`**（LICENSE 带自定义版权头时），脚本会拉 `repos/X/license` 读原文重新定性；`fair-code / Sustainable Use / BSL / SSPL` 会标 ⚠ 提醒「源码可见但非标准开源」。

## 二、抓素材（fetch_repo.py）

产出：
```
<WORKDIR>/repo.json    元数据 + 素材清单（写脚本时直接读它）
<WORKDIR>/assets/      NN_demo.gif / NN_shot.png —— 按可用性排序
<WORKDIR>/og.png       GitHub 社交卡片图（做"亮相"场景很合适）
```

素材优先级（脚本已按此排序）：
1. **README 顶部的 demo GIF** —— 现成的演示动画，比自己录屏快十倍，渲染时会被叠成动态前景
2. **dashboard / 界面截图**（大尺寸 PNG）—— 展示产品形态
3. **benchmark / 架构图** —— 讲原理和性能时用
4. `og.png` 卡片 —— 正式"介绍这是什么"的场景

**用户直接提供的素材**（截图 + 录屏）优先级高于 README 抓取结果：复制到 `<WORKDIR>/assets/`
后按同一套流程用即可。录屏（mp4）是最好用的动态素材——先用
`ffmpeg -i x.mp4 -vf "fps=1/8,scale=900:-1" f%02d.png` 每 8 秒抽一帧看清时间轴，
再把最有戏的几秒用 `asset_start` 分配给不同场景（同一段录屏可服务多个场景）。

脚本已自动剔除徽章（shields.io / codecov / 访客计数等）和小于 `--min-px` 的图标。
**注意**：模型若无法直接读图，用 `volcano-tts-video` 技能里的 `ocr_dir.ps1` 跑 Windows 内置 OCR 把每张图上的文字读出来，据此决定素材怎么分配到场景（这一步很关键，决定解说词和画面是否对得上）。

### 抓到的图要先看尺寸，长截图/竖图必须先处理

素材区是 contain 适配，所以**素材的长宽比直接决定它在画面里有多大**：

| 素材形态 | 不处理的后果 | 处理方式 |
|---|---|---|
| 超长截图（如 1464×9187 的聊天记录） | 等比缩到 9187→736，整图变一条 118px 宽的细线 | 裁出有信息的区域再入片 |
| 竖版手机截图（750×1250 的推送效果图） | 横屏里只占画面宽度 ~28%，四周全是模糊背景 | 用 `compose_wide.py` 做宽幅合成图（下条） |
| 1440 宽的长列表图（2030 高） | 高度撑满、宽度只用到 540px | 裁成 4:3 左右（如 1440×1100），宽度立刻用满 |

**竖版手机截图 → 横屏宽幅（`scripts/compose_wide.py`）**：把「整屏」放左、把「关键区域放大」放右，
合成 1600×1000 的 1.6:1 图，横屏里能铺满；放大版承担"让人看清"的职责。

```bash
python scripts/compose_wide.py --src assets/push.png --out assets/push_wide.png --zoom 120,740
python scripts/compose_wide.py --src shots_dir --out assets --batch        # 批量
```

`--zoom y0,y1` 是放大区域在原图中的纵向范围（横向始终取满宽），不给就取中间 60%。
裁图/合成完记得把 `script.json` 里的 `asset` 换成新文件——**别原地覆盖正在渲染的素材**，
两版画幅共用一个 assets 目录，中途换图会让先渲的那版前后不一致。

## 三、写脚本（script.json）

结构（完整示例见 `references/script_template.md`）：
```json
[{"idx":1, "kicker":"钩子", "caption":"画面大字", "text":"口播文案", "asset":"assets/01_shot.png"}]
```

视频素材额外加 `asset_start`（起始秒）：`{"asset":"assets/demo.mp4", "asset_start":38}`。

**六段叙事公式**（1 分钟装得下一个项目）：

| 段 | 作用 | 写法 |
|---|---|---|
| 1 钩子 | 前 3 秒决定完播率 | 痛点反驳 / 反常识 / 结果前置，别做铺垫 |
| 2 亮相 | 它是什么 | 一句话定位 + 最大特征 |
| 3 原理 | 凭什么做到 | 讲清最核心的那个机制，1 个就够 |
| 4 卖点 | 具体怎么用 | 数据或对比，给画面呈现的东西配词 |
| 5 亮点 | 最抓眼的一处 | 挑最有视觉冲击的功能 |
| 6 引导 | 去哪找 | 协议 + 项目名 + Star 数 |

### 字数预算（必须先算再写）

```
配音预算 = 目标时长 T − N × (lead + tail)      # 默认 lead=tail=0.35
全文目标字数 ≈ 配音预算 × 字/秒                 # 字/秒见下方，随音色和语速变化很大
```

实测基准（pipeline 真实产出，含标点）：
- **磁性解说男 @1.2x ≈ 4.8 字/秒**；**广告解说男 @1.3x ≈ 5.5 字/秒**
- 同一句文案横向比：广告解说比磁性解说**慢 6%**，解说小明**快 27%**，小天**快 32%** —— 换音色后必须重算字数，别套用旧值
- 每段还有约 0.15s 的段内固定开销（mp3 首尾静音），场景越多越明显

按时长的经验值：
- **60 秒 → 8 场 / 约 290 字**（每段 36 字），或 7 场 / 230 字
- **90 秒 → 9 场 / 约 340 字**
- **120 秒 → 11 场 / 约 480 字**

- 字数超了就删最长的 2~3 段（砍形容词、重复主语，保留数字和卖点）
- **数字写中文**（"百分之八十九"、"三万三千"），否则 TTS 容易读错；英文专名（项目名、模型名）保留原样
- `caption` 是画面大字，**≤ 14 字**，一行读完；`text` 是口播，可以更长
- 装饰性/OCR 读不准的数字**不要写进文案**，交给画面自己呈现
- 别用"最好/第一/唯一"这类绝对化表述（广告法风险）

## 四、配音（make_audio.py）

```bash
# 默认：磁性解说男声 1.2x
python scripts/make_audio.py --dir <WORKDIR> --target 60

# 换音色 + 换语速（务必带 --force，否则旧音色片段被静默复用）
python scripts/make_audio.py --dir <WORKDIR> --speaker zh_male_guanggaojieshuo_uranus_bigtts \
       --speed-rate 30 --force --target 60
```

**音色（seed-tts-2.0，只支持 `*_uranus_bigtts`，实测均可用）**：

| 音色 ID | 名称 | 风格 / 适用 |
|---|---|---|
| `zh_male_guanggaojieshuo_uranus_bigtts` | 广告解说 2.0 | **播报腔**，最像电视广告旁白，推广片首选 |
| `zh_male_cixingjieshuonan_uranus_bigtts` | 磁性解说男声 2.0 | 解说感，语气偏温和（旧默认） |
| `zh_male_jieshuoxiaoming_uranus_bigtts` | 解说小明 2.0 | 解说腔，节奏最快之一 |
| `zh_male_gaolengchenwen_uranus_bigtts` | 高冷沉稳 2.0 | 新闻播报质感，沉稳 |
| `zh_male_taocheng_uranus_bigtts` | 小天 2.0 | 清爽磁性，年轻化 |
| `zh_female_liuchangnv_uranus_bigtts` | 流畅女声 2.0 | 视频配音，女声播报 |
| `zh_female_zhixingnv_uranus_bigtts` | 知性女声 2.0 | 女声播报，知性 |

- 语速 `--speed-rate`：20 = 1.2x，30 = 1.3x，35 = 1.35x（用户说"快一点"一般取 30~35，别超过 40，会失去播报感）
- 环境变量也可覆盖：`VOLC_TTS_KEY` / `VOLC_TTS_SPEAKER` / `VOLC_TTS_RATE`

**火山 TTS Key 怎么配**（源码里不留 Key，避免误提交）：
按顺序取，先命中先用——

1. 环境变量 `VOLC_TTS_KEY`
2. `scripts/.volc_key` 文件（一行纯文本，已写进 `.gitignore`，**只存在于本机，不进仓库**）

两者都没有时脚本会直接报错并提示怎么写。换机器 / 重新 clone 后，
先补上其中一处再跑 `make_audio.py`，否则第四步会卡在缺 Key。
- **按文件存在性缓存**：改稿后只删 `<WORKDIR>/tts/sNN.mp3` 里要改的那几段，重跑只合成它们（几秒钟），其余命中缓存
- ⚠️ **换音色 / 改语速时必须 `--force`**：缓存只认文件名，不加 `--force` 会静默复用旧音色，封面/画面都换了、声音还是老的
- 每场景音频 = `lead 静音 + 配音 + tail 静音`，画面时长严格等于音频时长 → **天然音画同步，不需要任何对齐处理**

## 五、背景音乐与音效（make_soundtrack.py）

**全部程序化合成（numpy），不需要任何素材文件，也没有版权问题**——这是刻意设计：
BGM 素材要么有版权风险，要么要塞给用户一个几 MB 的文件，都不如现场合成可控。

产出 `<WORKDIR>/soundtrack.wav`（48kHz 立体声）；`make_video.py` 检测到它就自动优先使用。

**BGM 编曲**：84 BPM，Am7–Fmaj7–Cmaj7–G6 四小节循环，铺 pad + 低音 + 软底鼓 + 沙锤 + 琶音，
整体低通到 7kHz 给口播让中高频。

**人声侧链闪避（ducking）**——这是"音乐不打架"的关键：
按配音包络动态压 BGM，说话时压低、间隙抬回，所以转场处音乐会"涌"一下，反而强化了节奏。

**转场音效**：每个场景边界一个 whoosh（提前 0.34s 扬起，峰值压在切换点）+ 轻 blip；
末屏 CTA 前加 riser + impact；开场一个上行 whoosh。

### 调音旋钮

```bash
--bgm-gain 0.75     # 觉得音乐吵就调小（默认 1.0）
--sfx-gain 0.6      # 音效太抢就调小
--gain-db 3         # 整条片子偏轻时整体加 3dB
--no-bgm / --no-sfx # 单独关掉
--dump-stems        # 额外导出 stems/voice|bgm|sfx.wav，可单独试听定位问题
```

### 混合比例（默认值，照此判断是否异常）

| 项 | 目标 | 说明 |
|---|---|---|
| 说话时 BGM 低于人声 | **10~14 dB** | 低于 8dB 会抢词，高于 18dB 等于没音乐 |
| 间隙时 BGM 抬升 | **4~12 dB** | 这个落差就是"转场涌一下"的来源 |
| BGM 在 400–4000Hz | 别低于人声 26 dB | **手机外放基本不还原 400Hz 以下**，音乐只剩低频等于没音乐 |
| 成片真峰值 | ≤ −1 dBFS | 超了脚本会自动整体衰减 |

### 客观验收（听不到也能验）

```bash
python scripts/verify_soundtrack.py <WORKDIR>   # 需先 --dump-stems
```
检查四件事：① BGM 自相关在 1.429s（鼓点脉冲）和 2.857s（小节呼吸）处有正峰；
② 闪避深度落在上表区间；③ 音效落点是否卡在场景切换点；④ 各频段分布与手机外放可听性。

再整体测一次响度（本条成品约 −20 LUFS / 真峰值 −1.8 dBFS，各平台会自行做响度归一）：
```bash
ffmpeg -i <成片.mp4> -af ebur128=peak=true -f null - 2>&1 | grep -E "I:|TPK:"
```

## 六、渲染（make_video.py）

两套版式由 `layout(W,H)` 按画幅切换，配色是 GitHub 暗色系（#0d1117 底 / #2ea043 绿 + #6ee794 高亮）。

**竖屏 9:16（1080×1920）** —— 适合抖音/视频号/小红书/Shorts：

```
y 78    顶部栏：首字母头像 + owner/name + 语言·协议 + ★star 胶囊 + 页码
y 232   素材区（918 高）：同图 cover 模糊压暗做背景，前景 contain 居中 + 圆角 + 投影
y 1216  kicker 小标签（绿色胶囊，如"原理""内存分层"）
y 1292  画面大字 caption（62px，居中）
—       1640 以下留空：避开短视频 App 的底部 UI 遮挡区
封面见第七节
```

**横屏 16:9（1920×1080）** —— 适合 B 站/YouTube/官网嵌入：

```
y 44    顶部栏：头像 + owner/name + 语言·协议（左）· 仓库网址 + ★数据截至（右）
y 142   素材区（736 高，铺满宽）
        └ 画面内叠字：★star 胶囊 + 页码徽标（右上角）、kicker 标签（左下角）
y 902   画面大字 caption（58px，左对齐 + 绿色竖条，最多两行）
y 1050  底部进度轨（按场景进度填充）
封面见第七节
```

横竖屏共同的画面处理：
- 静态图场景加 1.0→1.035 微推镜 + 首尾淡入淡出
- **GIF / 视频素材**叠成动态前景（背景仍用其中一帧铺模糊底），这是最抓眼的部分，优先给动态素材安排场景
- 动态前景与静态图同口径铺满素材区，横屏字幕在画面外，**不需要额外避让**（早期给动态素材多减 300px 的写法会把录屏压到三分之一大）

### 文字效果（这一层是观感差异的主要来源）

| 元素 | 效果 |
|---|---|
| 画面大字 | 柔和投影 + **竖向渐变填充**（上亮下暗，比纯白更有质感） |
| 关键词 | 在 `caption` 里用 `**高亮**` 标记 → 强调绿 + 外发光 |
| 标签/星标/页码 | 半透明圆角胶囊 + 描边；kicker 额外带一圈绿色光晕 |
| 顶部仓库名 | 渐变字 + 投影 |
| 折行 | 内置中文**禁则**：收尾标点不会落到行首，也不会出现只剩 1~2 字的孤字行 |

`**` 标记同时作用于竖屏、横屏与封面；写 caption 时把数字和反差词框起来（如 `每个 token 只激活 **5%** 参数`），
这是最省力的强调手段。

## 七、封面（make_cover.py）—— 决定点击率的一半

封面**不是截一帧**，而是一张专门设计的缩略图：它在信息流里只有 ~200px 宽，只放三层信息——
**一句反差大字 + 一个主体（软件界面）+ 一个行动指引**。`make_video.py` 渲完会自动调它，也可单独跑：

```bash
python scripts/make_cover.py --dir <WORKDIR> --ratio both
```

产出三张（横竖都有，一次出全）：

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `cover_16x9.png` | 1920×1080 | B站 / 官网嵌入 |
| `cover_youtube_1280x720.jpg` | 1280×720 · ~130KB | YouTube（规范尺寸，远低于 2MB 上限）|
| `cover_9x16.png` | 1080×1920 | 抖音 / 视频号 / 小红书 |

### 文案：`<WORKDIR>/cover.json`（建议手写，别用自动推导）

```json
{
  "badge": "开源精选",
  "headline": "一个 **10MB** 的软件\n替掉七款工具",
  "sub": "SSH · RDP · 数据库 · 容器 · 内置 AI Agent",
  "cta": "GitHub 搜 uniterm",
  "asset": "assets/demo.mp4",
  "asset_start": 53,
  "callouts": [{"text": "AI 自主执行", "x": 0.70, "y": 0.15}]
}
```

- 没有 cover.json 也能跑：标题取 script.json 第 1 场的 caption，副标题取 repo 元数据，素材取第 1 场素材
- 命令行参数（`--headline / --sub / --badge / --cta / --asset / --start / --accent`）优先于 cover.json
- `headline` 里的 `\n` 强制换行，`**...**` 出琥珀色高亮（与 caption 同一套语法）

### 非 GitHub 项目（VPS / SaaS / 软件测评）怎么用

`repo.json` 是**可选**的。没有它时，把等价字段直接写进 `cover.json`，
脚本会自动接住，版式代码不用改：

| 字段 | 对应原来 repo.json 的 | 说明 |
|---|---|---|
| `name` | `name` | 左上角主体名（如 `BandwagonHost`）|
| `owner` | `owner` | 有则渲染成 `owner/name`；留空只显示 `name` |
| `stars` | `stars` | 有值才出右上角金色星标胶囊 |
| `eyebrow` | — | **没有 stars 时的右上角替代胶囊**，琥珀色小标签，放一句规格亮点（如 `2核 1G 20G`）|
| `meta` | `language · license · 开源` | 覆盖左上角主体名下方那行灰色小字 |
| `title` | — | 卡片窗口标题栏文字 |

```json
{
  "name": "BandwagonHost",
  "title": "BandwagonHost — VPS Hosting",
  "badge": "VPS 实测",
  "eyebrow": "2核 1G 20G",
  "meta": "洛杉矶 USCA_2 机房",
  "headline": "**49美元**一年\n搬瓦工VPS实测",
  "sub": "购买 · 配置 · 跑分 全流程实测",
  "cta": "看完整评测",
  "asset": "assets/main2.png",
  "accent": [232, 138, 18]
}
```

- **`accent` 换品牌色**：默认是 GitHub 绿。搬瓦工这类橙黄系品牌用 `[232,138,18]` 更贴，
  但要和 `AMBER` 高亮色（255,199,61）拉开亮度差，否则大字高亮会糊在强调色里。
  **必须选中等明度的色**——版式里徽标文字硬编码深色 `(6,24,12)`、CTA 文字硬编码白色 `(250,255,252)`，
  所以太亮的色（薄荷绿/浅黄）会让 CTA 白字发虚，太暗的色（深蓝/番茄红）会让徽标深字发糊。
  参考：亮蓝青 `[0,150,210]`、品牌蓝 `[26,110,200]` 都两头通吃；`[30,120,235]` 就偏暗了。
  另外 accent 同时用在背景光束与主体光晕上，**底色是近黑navy**，冷色 accent 容易糊进背景，
  选色时看一眼渲染图左侧大字与背景的分离度
- **`badge` / `cta` 必须手写**：默认值是「开源精选」「GitHub 搜 xxx」，非 GitHub 项目会驴唇不对马嘴
- **截图里的数字要先核实再用**：封面是唯一会被单独传播的东西。中国→美西的 ICMP 延迟物理下限约 67ms，
  若测速网站显示 40~50ms，说明那不是直连延迟（多半是 CDN 边缘节点），**不能当成卖点写上封面**

### 写封面文案的硬规则（不遵守就白做）

- **单行 ≤ 6 个全角字符等效宽度**（数字/英文按 0.55 折算）。超了字号会被自动压小，字就没气势了。
  「一个 10MB 的软件」≈ 6.7 个全角 → 两行版式下字号正好落在 120~130px
- **两行封顶**：第一行给"对象"，第二行给"结果/反差"，数字必须出现（`10MB`、`30+`、`七款`）
- `sub` 一行、≤ 22 个全角宽度，放协议/技术栈这类"证明它靠谱"的信息
- `cta` 短、动词开头：`GitHub 搜 xxx`
- **不要把视频里的原句直接搬过来** —— 封面是标题党，视频是内容

### 版式（已定型）

```
左列：徽标 + owner/name + 语言·协议 → 大字（带高亮）→ 品牌色强调条 → 副标题 → 行动胶囊
右列：主体卡片 = 截图 + 窗口外框（红黄绿三点）+ 投影 + 品牌色光晕
右上：金色星标胶囊（无 stars 时换成 `eyebrow` 胶囊）
背景：暗底渐变 + 斜向光束 + 点阵 + 品牌色光斑
```

- 大字用**黑色描边 + 投影**，缩到 200px 宽也能读；高亮段额外加琥珀色光晕
- `callouts` 用来圈重点（模仿"圈出来"的封面习惯），坐标是相对卡片的 0~1 比例，**`y` 建议 ≥ 0.15**，否则会压住窗口标题栏
- **封面素材别用白底空界面截图**：浅色大面积留白的截图放进卡片会显得空。优先挑信息密度高的那一帧
  （如 AI 面板 + 表格），录屏用 `asset_start` 定位——和正片用同一段录屏也没关系，封面本来就是挑最抓眼的一帧
- 素材缺失时自动退化成"项目名片"（首字母 + 仓库名 + 网址），右半边不会空
- **宽录屏（2:1 左右）做卡片会留白**：卡片框是 900×700，2:1 素材只能占 900×450，
  上下各空一大块。把**两段不同时期的画面裁成同宽、竖着拼成近 4:3**，卡片就能撑满，
  顺便在一张图里讲完"买什么 + 实测结果"：

  ```bash
  ffmpeg -y -ss 62 -i <录屏.mp4> -ss 500 -i <录屏.mp4> -filter_complex \
    "[0:v]crop=640:240:0:80,scale=1280:480:flags=lanczos,pad=1280:488:0:0:color=0x2b3644,unsharp=5:5:0.5:5:5:0[a];\
     [1:v]crop=640:208:0:46,scale=1280:416:flags=lanczos,unsharp=5:5:0.5:5:5:0[b];\
     [a][b]vstack=inputs=2[out]" -map "[out]" -frames:v 1 assets/main2.png
  ```

  - `pad=...:ih+8` 那条 8px 深色缝是刻意的分隔线，让两块截图看起来像"拼贴"而不是"截错了"
  - 上块取"产品/价格"信息，下块取"运行/数据"画面，**两块别取同一个界面**（信息重复＝浪费半个封面）
  - 每个 `crop` 单独试几组 y 偏移再拼：录屏常带窗口标题栏、Xshell 状态栏、桌面杂边，切掉才干净
  - 效果：画面自己讲完了"全流程"，`sub` 那行就不必再罗列流程

## 八、交付

竖屏直接发抖音/视频号/小红书/Shorts；横屏发 B 站/YouTube，也能嵌官网。
封面直接用第七节产出的三张（YouTube 传 `cover_youtube_1280x720.jpg`，B站传 `cover_16x9.png`）。
发布时标题带「项目名 + Star 数 + 一个反差结论」，别用"最好的 XX"。Star 和版本号天天变，视频里标注「截至 X 年 X 月」。

## 九、踩过的坑

1. **中文文件名输出**：ffmpeg 在某些构建下写中文路径会产出 0 字节（`moov atom not found`）→ 输出用 ASCII 文件名，完成后再改名
2. **PowerShell `2> file` 的日志是 UTF-16 LE** → Python 读它必须 `encoding="utf-16"`，否则正则全匹配不到
3. **PowerShell 工具 stdout 常不回显** → 结果写临时文件再用 Read 读
4. **`& $exe | Out-File` 在 stderr 有内容时返回非 0 退出码**，但任务可能实际成功 → 判断成败要读日志文件，不能只看退出码
5. **PowerShell 删文件会静默不生效** → 一次 `Remove-Item "a","b"` 不行；`foreach` + `Remove-Item -Force`
   也可能整批没删掉且**不报错**（实测踩到过）。最可靠的是直接调 .NET：
   `[System.IO.File]::Delete($p)` / `[System.IO.Directory]::Delete($p, $true)`，
   删完**必须 `Test-Path` 复核**再继续，否则会以为已清理而其实没清。
   注意本机安全删除网关有时"报错但实际已删"（返回 `[safe-delete][SAFE_DELETE_FAIL_CLOSED] trash-failed`
   而 detail 里写着目标已处理）→ 同样别据返回值判断成败；
   目录删不掉时改用 `Move-Item` 归档到 `_tmp/` 更稳
6. **ffmpeg 不在 PATH** 时用 winget 安装的绝对路径，脚本里 `find_ffmpeg()` 已自动处理
7. **旋律/鼓点写进低频区会"消失"**：合成 BGM 时若把音色全放在 400Hz 以下，手机外放听不到 →
   必须有中音区层（本脚本的 `ch[1]/ch[2] + 12` pad 层与琶音就是为此）
8. **片段缓存不能用幻灯片的 mtime 判定**：幻灯片每轮都重生成，会让缓存永远失效 →
   要用 `script.json` / `repo.json` / 素材的 mtime 判定
9. **片段缓存必须带画幅**：缓存文件名是 `clips/vNN_宽x高.mp4`，只写 `vNN.mp4` 会让竖屏/横屏互相复用错画面
10. **别在媒体区压大面积 scrim**：浅色截图（benchmark 白底图）会被压成灰雾；胶囊自带不透明底，浅底上也读得清
11. **箭头只能用 `→`**：微软雅黑里 `▸ ▶ ❯` 的字形是"豆腐块"（而且 `getbbox()` 有返回值，光查 API 查不出来，必须肉眼渲染一遍）
12. **PIL 的 `paste` 只吃整数坐标**：`x` 传 float（居中对齐时很容易算成 float）会抛
    `TypeError: 'float' object cannot be interpreted as an integer`，贴图前统一 `int(round(...))`
13. **封面文案要先算字号再版式**：标题长度不可控，得先 `plan_headline()` 定字号和行数、算出块高，
    才能把整块垂直居中；不先算就会出现"字大时压住 CTA、字小时飘在半空"
14. **两种画幅必须串行渲染，别并行**：`slides/sNN.png` 的文件名不含画幅，两版同时跑会互相覆盖，
    导致某版片段里混进另一版版式的画面。缓存按画幅分开存（上条），但幻灯片是共用的
15. 环境依赖：`gh`（已登录）、`httpx`、`numpy`、`Pillow`、`ffmpeg`
