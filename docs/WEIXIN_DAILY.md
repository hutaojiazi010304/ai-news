# 微信公众号每日推文（WeChat Daily Article）

每天在本地把 `data/daily-brief.json`（伯乐精选）整理成一篇排版好的公众号推文，
推送到仓库后由在线预览页展示，你从手机复制粘贴进公众号编辑器即可发布。

- 预览页：`https://<你的用户名>.github.io/ai-news-radar/weixin/`
- 数据来源：`data/` 由云端工作流 `update-news.yml` 每小时持续更新；推文生成只在本地消费它的产物
- 实现：`scripts/generate_weixin_article.py`
- 为什么不用 GitHub Actions：千问 API key 有 IP 白名单限制，只能在本地网络调用，
  因此自 2026-08 起改为每天本地手动运行（原 `.github/workflows/weixin-daily.yml`
  已移除，需要时可从 git 历史找回）。

## 工作原理

```
data/daily-brief.json（现有管线产物，云端每小时更新）
        │  git pull
        ▼
本地运行 scripts/generate_weixin_article.py
  ├─ 选条：按 peak_score 降序取前 20 条（故事在窗口内达到过的最高
  │     重要度，由管线持久化；旧数据缺该字段时回退 importance_score，
  │     避免早间重要条目在出刊时因新近度衰减排到后面）
  ├─ 类别刷新：云端落盘的 category 是故事创建时算的，白名单更新前的
  │     旧故事会带着错误标签（如官方博客停留在「行业动态」）。出刊时按
  │     当前 aihot 官方一手源白名单复核主条目来源，命中即纠正为「官方
  │     更新」（只升不改其他类别），两个排版版本共用此逻辑
  ├─ 每条导读：配了千问 key 时统一由千问生成（直接概括内容、正文缺失
  │     时按标题整理；无意义话术、字数跟随内容，21天缓存），
  │     仅在生成失败时回退到数据里已有的推荐语；无 key 时复用已有推荐语
  ├─ 导读取材：优先用数据里的 summary 字段（云端管线从官方 RSS 摘要
  │     落盘的纯文本，离线可读、不受原文站反爬影响）；summary 缺失、
  │     过短或纯模板话术时才实时抓原文页（先直连，再 r.jina.ai 兜底）
  ├─ 每条信息下附原文链接（纯文本 URL）
  ├─ 标题：固定模板「AI 雷达 · X月X日｜今日精选N条」（不调千问，与 key 无关）
  ├─ 摘要：固定模板（≤120字）
  ├─ 封面：三级兜底
  │     A. 千问先把头条翻译成紧贴 AI 内容的具体画面描述（可含品牌
  │        商标、少隐喻），再交 qwen-image 生图（2.35:1，裁剪到
  │        1664×708）；翻译失败回退原始头条
  │     B. 头条含负面词或 A 失败 → 品牌模板 prompt 重试
  │     C. 再失败 → 仓库内静态图 assets/weixin-cover-fallback.png
  └─ 渲染：内联样式 HTML（公众号编辑器只认 inline style）
        │
        ▼
weixin/index.html   预览页（每条附原文链接；底部附标题/摘要/阅读原文纯文本，方便手机复制）
weixin/meta.json    title/digest/cover/read_more_url（将来接 API 草稿箱直接消费）
weixin/cover.jpg|png  封面
weixin/reason-cache.json  推荐语缓存（21 天过期，随 git 持久化）
```

**没有 API key 也能跑**：推荐语复用数据里已有的（长文直接用、短文兜底）、标题走
模板、封面用静态图，始终 exit 0。key 只用于增强，不是必需。

## 一次性配置（只做一次）

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

推文生成只硬依赖 `requests`；`Pillow` 负责把 AI 生成的封面裁剪成 2.35:1，
没装时封面自动退回静态图。装齐 `requirements.txt` 时，选条还会按管线的官方
一手源白名单刷新故事类别（见工作原理「类别刷新」）；依赖不全时跳过该步、
直接信任数据里落盘的类别。

### 2. 千问 API key 保存在本地

key 有 IP 白名单限制，只在本地网络可用。建议设为系统环境变量，或每天在终端临时
设置（见下文）。**不要把 key 写进任何会提交的文件**（`.env*` 已被 gitignore）。
仓库里的 GitHub Secret `DASHSCOPE_API_KEY` 与 `WEIXIN_*` Variables 已无工作流
使用，可以删除。

### 3. 确认 Pages 已开启

Settings → Pages → Source：Deploy from a branch → Branch: `master` / `(root)`。

### 4.（可选）自定义参数

都是本地环境变量，不设置就用默认值：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WEIXIN_BRAND_NAME` | `AI 雷达` | 公众号名定了以后改这里 |
| `WEIXIN_RADAR_URL` | `https://hutaojiazi010304.github.io/ai-news-radar/` | 「阅读原文」链接 |
| `WEIXIN_MAX_ITEMS` | `20` | 每期条数 |
| `WEIXIN_TEXT_MODEL` | `qwen3.8-max` | 文本模型 |
| `WEIXIN_IMAGE_MODEL` | `qwen-image-2.0-pro` | 生图模型（Qwen-Image 同步接口系列，如 `qwen-image-max`） |
| `DASHSCOPE_API_BASE_URL` | DashScope 兼容模式地址 | 一般不用改 |
| `WEIXIN_ENABLED` | （开启） | 设为 `0` 临时停刊 |

## 每日出刊流程（手动，每天一次，约 5 分钟）

1. 更新代码与数据到最新（`data/` 由云端管线生成，以云端为准）：

   ```
   git config --global http.proxy http://127.0.0.1:7897
   git config --global https.proxy http://127.0.0.1:789
   git config --global --unset http.proxy
   git config --global --unset https.proxy
   ```
   ```bash
   git checkout master
   git pull
   ```

   本仓库已配置 `pull.rebase=true` + `rebase.autoStash=true`：pull 会把
   本地提交 rebase 到云端最新快照之后，未提交的改动自动 stash/恢复，
   不会产生 merge 提交，也不会弹编辑器。

   **注意**：`weixin/` 产物每次生成后都要及时提交推送，别留在工作区（可用`git status`查看）
   （autostash 只管已跟踪文件；若某个未跟踪的产物文件恰好被远端历史
   跟踪，rebase 会拒绝切换分支）。pull 前发现 `weixin/` 有未提交的旧
   产物：要留就先 `git add weixin/ && git commit` 再 pull，不要就
   `git checkout -- weixin/` 并删除多余文件后再 pull。github 直连
   不通的环境请用代理终端执行。

2. 设置 key（仅当前终端，勿写入文件）：

   ```cmd
   set DASHSCOPE_API_KEY=你的key
   ```

   PowerShell 用 `$env:DASHSCOPE_API_KEY="你的key"`，macOS/Linux 用
   `export DASHSCOPE_API_KEY=你的key`。

3. 生成推文：

   ```bash
   python scripts/generate_weixin_article.py --data-dir data --output-dir weixin
   python scripts/generate_weixin_article.py            --data-dir data --output-dir weixin
   python scripts/generate_weixin_article_grouped.py    --data-dir data --output-dir weixin-grouped
   ```

   看结束时的汇总日志确认 key 生效：
   `items=20 … generated=… cover_mode=headline|brand cover_scene=1|0`。
   标题固定为模板「AI 雷达 · X月X日｜今日精选N条」，不再调用千问；
   `cover_mode=static` 说明 key 没生效或生图失败（见 FAQ）；
   `cover_scene=0` 说明场景翻译失败、封面主题回退为原始头条。

4. 打开 `weixin/index.html` 检查各条导读、原文链接，以及底部辅助区块里的标题/摘要。

5. 提交并推送，Pages 会自动更新预览页：

   ```bash
   git add weixin/
   git commit -m "chore: update weixin article"
   git push
   ```
   如果报错就
   git pull --rebase --autostash
   git push


6. 手机取稿（预览页更新后）：打开
   `https://hutaojiazi010304.github.io/ai-news-radar/weixin/`
   （若手机/微信浏览器显示旧内容，是缓存：URL 后加 `?t=当天日期`
   强制刷新，或改用外部浏览器/无痕模式打开）
   - 长按选中正文 → 复制 → 粘贴进公众号编辑器正文
     （页面底部的灰色「发布辅助信息」区块不要复制进去）
   - 标题、摘要：从页面底部辅助区块复制（与 `weixin/meta.json` 一致）
   - 封面：保存 `weixin/cover.jpg`（或 `.png`），上传为封面图（2.35:1）
   - 「阅读原文」链接：填辅助区块里的地址（= 雷达主页）

## 分组对比版（weixin-grouped/）

与正式版**并存**的备选排版：选条、导读、标题、摘要完全一致（直接 import
正式版脚本的函数），只是正文按故事类别分组显示——官方更新 → 行业动态 →
多源热议 → 值得关注，空分组跳过，组内保持 peak_score 排序、编号重新从 ① 开始。
每组有居中的彩色分组标题（官方绿/行业蓝/热议红/关注灰），同组条目放在一个
带浅色底的大圆角框内。

```bash
# 先跑正式版（分组版要复用它的封面和导读缓存），再跑分组版
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-grouped
```

- 预览页 `https://hutaojiazi010304.github.io/ai-news-radar/weixin-grouped/`，
  与正式版 `/weixin/` 并排对比；最终定版后只保留对应脚本即可
- 导读缓存共享正式版的 `weixin/reason-cache.json`：导读按条目生成、与排版
  无关，先跑哪一版另一版都全命中，不会重复调千问
- 封面直接复用正式版当天的封面（按 `weixin/meta.json` 的 issue_date 校验，
  不会误用昨天的）；正式版还没生成时才走相同的封面生成流程
- 汇总日志 `cover_mode=reused` 表示复用了正式版封面；`sections=` 给出各组条数
- `weixin-grouped/` 产物同样要及时提交推送（同正式版的注意事项）

## 本地调试

```bash
# 无 key 冒烟（输出到已 gitignore 的 weixin-test/，不碰正式产物）
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin-test

# 只跑流程不写文件
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin --dry-run

# 分组版冒烟 / 只跑流程不写文件
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-test-grouped
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-grouped --dry-run

# 重新生成静态兜底封面
python scripts/generate_weixin_article.py --make-fallback-cover --assets-dir assets

# 测试（需 pytest）
python -m pytest tests/test_weixin_article.py tests/test_weixin_article_grouped.py -q
```

## 常见问题

- **改了白名单，为什么旧故事的标签也变了？** 类别由云端管线在故事创建时
  落盘，白名单改动不会改写已在盘上的旧数据；但出刊时推文脚本会按当前白名单
  复核并纠正（只升为「官方更新」），所以两个排版版本立即生效。云端管线本身
  要在推送新代码之后才跟上。
- **为什么不用 GitHub Actions 生成？** 千问 API key 有 IP 白名单限制，Actions
  runner 调用必然失败，会降级成无 key 版本（模板标题、无千问导读、静态封面），
  还会覆盖本地生成的结果，因此改为本地手动运行。
- **忘了设 key / key 失效？** 汇总日志里 `cover_mode=static`、`generated=0`
  且 `cached=0`，产物是降级版（无千问导读、静态封面）；重新设置 key 后再跑
  一遍同一条命令覆盖即可。标题始终是固定模板，不受 key 影响。
- **封面是静态图？** 说明生图失败（key 无效 / 模型名不可用 / 网络），脚本会
  在 stderr 打印原因。可用环境变量 `WEIXIN_IMAGE_MODEL` 换成可用模型
  （需支持同步 `multimodal-generation` 接口，如 `qwen-image-2.0-pro`、
  `qwen-image-max`）。
- **粘贴进编辑器样式丢了？** 正文只用安全内联样式（无 `<a>`/`<img>`/class）；
  如个别样式丢失，属编辑器行为，内容不受影响。
- **原文链接为什么是纯文本、点不了？** 公众号正文不支持外链超链接，编辑器会
  去掉 `<a>` 标签，所以每条信息下方以纯文本给出原文 URL，读者可复制到浏览器打开。
- **想改排版/条数/品牌名？** 排版改 `scripts/generate_weixin_article.py` 的
  `render_article_html`；条数与品牌名用环境变量即可，无需改代码。
