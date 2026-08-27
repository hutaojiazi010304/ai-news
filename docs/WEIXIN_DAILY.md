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
  ├─ 英文标题兜底翻译：上游翻译链失效时，daily-brief 会残留纯英文标题。
  │     配了千问 key 时在写导读之前逐条译成中文（产品/公司/模型/人名
  │     保留英文原文），译文按原标题缓存（reason-cache 里的 tt1| 条目，
  │     1.0/2.0 共用，精读版独立）；翻译失败保留英文原标题，无 key 时
  │     原样保留，均不影响出刊
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
| `WEIXIN_MAX_ITEMS` | `20` | 每期条数（1.0/2.0） |
| `WEIXIN_DEEP_MAX_ITEMS` | `10` | 精读版每期条数（只影响 3.0，与上一行互不相干） |
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

2. 设置 key 与代理（仅当前终端，勿写入文件）：

   ```cmd
   set DASHSCOPE_API_KEY=你的key
   ```

   精读版要抓境外原文的正文与插图，直连不通的环境在同一终端补设本地
   代理（端口按你的代理软件实际值，Clash 默认 7897）：

   ```cmd
   set HTTPS_PROXY=http://127.0.0.1:7897
   set HTTP_PROXY=http://127.0.0.1:7897
   set NO_PROXY=dashscope.aliyuncs.com,aliyuncs.com,aibase.com,chinaz.com
   ```

   **`NO_PROXY` 必须一起设**：千问是国内服务，被代理中转会打断 SSL、
   导读生成失败（详见 FAQ「精读版跑了很久没输出/没结果」）；aibase 与
   插图 CDN（chinaz）同属国内，走代理同样会 SSL 失败导致插图抓不到
   （脚本对这两类请求已内置直连重试兜底，设了 `NO_PROXY` 只是省掉
   一次必然失败的代理尝试）。1.0/2.0 的摘要兜底抓取同样受益于代理，
   不设也能跑（境外条目按降级处理）。

   PowerShell 用 `$env:DASHSCOPE_API_KEY="你的key"`（代理同理
   `$env:HTTPS_PROXY=…`），macOS/Linux 用 `export …`。

3. 生成推文：

   ```bash
   python scripts/generate_weixin_article.py            --data-dir data --output-dir weixin
   python scripts/generate_weixin_article_grouped.py    --data-dir data --output-dir weixin-grouped
   python scripts/generate_weixin_article_deep.py       --data-dir data --output-dir weixin-deep
   ```

   看结束时的汇总日志确认 key 生效：
   `items=20 … generated=… cover_mode=headline|brand cover_scene=1|0`。
   标题固定为模板「AI 雷达 · X月X日｜今日精选N条」，不再调用千问；
   `cover_mode=static` 说明 key 没生效或生图失败（见 FAQ）；
   `cover_scene=0` 说明场景翻译失败、封面主题回退为原始头条。
   分组版汇总另有 `sections=` 各组条数；精读版运行时逐条打印进度
   （导读/插图各一行），汇总形如
   `items=10 … images found=N missed=M cover_mode=item elapsed=…s`，
   `images missed` 是抓不到原文插图的条数（正常现象，该条无图出刊）；
   `cover_mode=item` 表示封面取自头条（或按评分降序第一个有图的）条目
   插图，`reused`/`static` 表示整期无图走了旧兜底。

4. 打开 `weixin/index.html` 检查各条导读、原文链接，以及底部辅助区块里的标题/摘要
   （分组版/精读版同样检查各自的 `index.html`，精读版还要看图与「图源」行）。

5. 提交并推送，Pages 会自动更新预览页：

   ```bash
   git add weixin/ weixin-grouped/ weixin-deep/
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
  与正式版 `/weixin/` 并排对比。三个版本（正式/分组/精读）按产品决策长期并存，
  各自独立保留
- 导读缓存共享正式版的 `weixin/reason-cache.json`：导读按条目生成、与排版
  无关，先跑哪一版另一版都全命中，不会重复调千问
- 封面直接复用正式版当天的封面（按 `weixin/meta.json` 的 issue_date 校验，
  不会误用昨天的）；正式版还没生成时才走相同的封面生成流程
- 汇总日志 `cover_mode=reused` 表示复用了正式版封面；`sections=` 给出各组条数
- `weixin-grouped/` 产物同样要及时提交推送（同正式版的注意事项）

## 精读版（weixin-deep/）

在分组版排版基础上做的「深度阅读」第三版，用于企业内部会话软件的服务号
内部分享（非公开公众号），与前两版**并存**：

- **选条**：纯按 `peak_score` 取全局前 20 条（`WEIXIN_DEEP_MAX_ITEMS` /
  `--max-items` 可调；与 `WEIXIN_MAX_ITEMS` 互不相干）。分组沿用
  官方更新 → 行业动态 → 多源热议 → 值得关注，空组直接跳过
- **导读**：转述式报道体（直接复述原文事实、不编造，无「据 X 报道」固定
  开头），控制在约 150–350 字、信息密度优先：正文抓取再全也只挑最核心
  的事实、数字与结论，不写背景铺垫、外界反应等外围内容。校验保留 450 字
  硬上限：初稿未过校验（超长/超短/夹带链接或解释性话术）会带强化提示
  重试一次，仍不过才按生成失败回退上游推荐语（硬上限本身是因「内容充实
  可适当更长」曾导致导读普遍变长、夹带热议观点而撤销的）。**缓存独立**于正式版：写在
  `weixin-deep/reason-cache.json`
  （自己的版本号）。不能共享 `weixin/reason-cache.json`——缓存 key 只有
  条目+标题，共享会永远命中旧短导读，新风格无法生效
- **信源行**：精读导读正文不提信源名——生成时不注入「信源：X」行，提示词
  明确正文不要提及信源（此前注入的信源行让官方更新板块的导读开头清一色
  "Official AI Updates 发布/披露"）。信源归属只在条目下方 meta 行显示；
  聚合筐名字（`Official AI Updates` 把各家官方渠道归在一个适配器下、
  `AI HOT` 是热点渠道聚合筐）不是真实发布方，显示时自动改用它旗下的具体
  渠道（`OpenAI News`、`GitHub Blog` 等），真实发布方（`AIbase` 等）
  原样保留。三个版本的 meta 行同此规则，署名跟着渠道走而不是清一色筐名。
  此外，一条故事的多个管线条目指向同一原文时（`source_count == 1`，只有
  一个独立出处），meta 行拆成「{来源} · 1 个来源 · {转载渠道} · N 个转载」：
  来源取管线选出的主条目（官方渠道优先），其余条目记为转载。真正多源
  （独立出处 >1）时渠道列表同样并入 meta 行：「{类别} · {渠道列表} · N 个
  来源」。两种情况都不再显示原来的「标题（渠道列表）」括号行（该行仅在
  故事标题为空时作为标题兜底保留）。渠道名展示时还会去掉尾部的抓取方式/
  出处注记（「Hacker News 热门（buzzing.cc 中文翻译）」→「Hacker News 热门」、
  「Qwen：Blog Retrieval（API）」→「Qwen：Blog Retrieval」）——每条本来就附
  原文链接，真实发布方以链接为准；数据里保留全名（官方一手源白名单按
  全名匹配）
- **关键句高亮**：导读生成之后，由**独立的第二步「标注」调用**用【】标出
  最值得读的片段（与导读生成分开两步、互不影响——合在一条提示词里会
  拉低导读质量）。选取原则：优先概括/结论性的短语或句子（含简短判断
  短语），其次是与主题紧密相关的关键名称或数字；先总说后展开的句子
  标冒号前的总说部分，不标段尾细节；总共不超过 4 处、可以更少。
  标注模型不得改动原文；若它仍顺手改动（最常见是补句号），脚本只取
  它选中的片段、在导读原文里重新锚定，导读本身一字不变；片段锚不
  上时重试一次，仍不行才退为无高亮。渲染时自动加粗并染成所在分组
  的颜色（与分组标题同色：官方更新绿、行业动态蓝等）。标记异常
  （不成对、单处包了大半个导读）同样自动纠正或退为无高亮，导读文字
  本身不受影响
- **导读质量与重掷**：导读由千问现场采样生成，提示词与参数不变时每次
  跑出的文稿也有天然差异（细节取舍、句式），属正常现象。某条不满意时
  不必整期重跑，用 `--regenerate` 只重掷该条（三个版本都支持，见本地
  调试）。参数值逗号分隔、可混写：**显示序号**（`3` 或 `③`，正式版即
  正文编号；分组版/精读版组内编号会重新从 ① 开始，序号按整期排名
  数）、**标题片段**（看到什么输什么，中文显示标题或英文原标题都行、
  忽略大小写）、**story_id**；`all` 全部重掷。未命中会打印本期编号清单
  供重试。1.0/2.0 删的是共享缓存，重掷一次两版同时更新
- **插图**：出刊时逐条抓原文页（直连，403/空页走 r.jina.ai 兜底；直连
  成功但整页定位不到正文的 JS 壳页——如 github.blog 前端渲染、`<article>`
  全是作者卡/推荐卡——也会再走一次 reader 代理，避免拿导航文字当导读
  素材），先定位正文范围（优先取 `<article>` 元素；页面没有该标签时按
  「AI News Recommendations / 推荐阅读 / 相关推荐」等推荐区标题截断），再取正文内
  第一张合格大图——推荐区缩略图不进候选（旧逻辑整页扫描，正文图下载
  一旦抖动就会错抓推荐新闻的图），跳过 logo/图标/追踪像素等，存到
  `weixin-deep/images/` 并在图下自动加「图源：{域名}」。排版上图片放在
  该条**导读之后**，按 65% 宽等比缩小、居中显示、不带圆角（宽度用脚本
  常量 `DEEP_IMAGE_WIDTH_PERCENT` 调整）。**正文完全没图时的推荐区借用**：
  用页面自身 H1 与推荐卡的标题（`<img alt>`）做二元组相似度比对，最高分
  ≥ 0.65 且领先第二名 ≥ 0.08（`REC_BORROW_MIN_SCORE` /
  `REC_BORROW_MIN_MARGIN`）才借用该卡的图，日志注明「推荐区同题报道」，
  `meta.json` 里该图标 `borrowed: true`；比对用页面 H1 而非中文标题
  （推荐卡标题是页面语言，aibase 为英文，跨语言永远配不上）。宁缺毋错：
  同产品不同事件、两张图难分伯仲等情况一律保持无图。正文没图且无可借用
  → 该条无图，属正常降级；不做 AI 补图。有 Pillow 时统一压到 ≤1080 宽、
  JPEG q82，仓库每天约增长 1–3MB
- **标题/摘要**：固定模板「AI 雷达 · X月X日｜今日精读N条」，不调千问
- **封面**：优先用**头条条目抓到的原文插图**中心裁切成 2.35:1
  （1664×708，复用 1.0 的 `crop_cover`）；头条没图时按评分降序取第一条有
  图的条目插图；整期都没图才回退旧链路（复用正式版当天封面 / 1.0 生图 /
  静态兜底）。汇总日志 `cover_mode=item` 即「用了条目插图」
- **取图不需要千问 key**：无 key 也能跑插图；只是导读会退化为数据里已有的
  推荐语

```bash
# 先跑正式版（精读版复用它的封面），再跑精读版
python scripts/generate_weixin_article.py      --data-dir data --output-dir weixin
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-deep
```

- 预览页 `https://hutaojiazi010304.github.io/ai-news-radar/weixin-deep/`
- **图片不进粘贴流**：复制预览页正文粘贴进编辑器时，`<img>` 会被丢掉。
  预览页是准绳视图；给内部服务号发布时按 `weixin-deep/images/` 里的文件
  逐张手动插入（`meta.json` 的 `images` 字段按 story_id 记录了每条对应的
  图片文件与图源，预留将来接草稿箱 API）
- 内部分发的图片版权：封闭传播 + 每条带图源说明，实务投诉风险很低；
  仅限内部使用，如收到异议撤换对应图片即可
- `weixin-deep/` 产物（含 `images/`）同样要及时提交推送

## 本地调试

```bash
# 无 key 冒烟（输出到已 gitignore 的 weixin-test/，不碰正式产物）
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin-test

# 只跑流程不写文件
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin --dry-run

# 分组版冒烟 / 只跑流程不写文件
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-test-grouped
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-grouped --dry-run

# 精读版冒烟 / 只跑流程不写文件（--no-images 跳过抓图，完全离线快速冒烟）
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-test-deep
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-test-deep --no-images
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-deep --dry-run

# 某条导读不满意，只重掷该条（三个版本都支持；参数可混写、逗号分隔：
# 显示序号 3 或 ③ / 标题片段，中英文都行 / story_id；all = 全部重掷。
# 未命中会打印本期编号清单，照着重试即可）
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-deep --regenerate 3
python scripts/generate_weixin_article_deep.py --data-dir data --output-dir weixin-deep --regenerate 连线、运行
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin --regenerate ③,⑤
python scripts/generate_weixin_article_grouped.py --data-dir data --output-dir weixin-grouped --regenerate Kiro

# 重新生成静态兜底封面
python scripts/generate_weixin_article.py --make-fallback-cover --assets-dir assets

# 测试（需 pytest）
python -m pytest tests/test_weixin_article.py tests/test_weixin_article_grouped.py tests/test_weixin_article_deep.py -q
```

## 常见问题

- **精读版跑了很久没输出/没结果？** 精读版每一步都有进度行（`[3/10] 导读：…`、
  `[3/10] 插图：…`，末行汇总含 `elapsed=总耗时秒数`），卡在哪一步看最后一行
  进度即可定位。两个高频坑：
  - **别用鼠标点运行窗口**：Windows 黑色控制台一旦被点中会进入「选择」模式，
    进程整个冻结，直到按 Enter 才继续（标题栏出现「选择」字样）。看上去像
    卡死，其实按一下 Enter 就恢复。
  - **境外原文直连不通**：系统没开代理时，境外页面（如 openai.com）与
    r.jina.ai 兜底都连不上，相关条目按「无图/回退上游推荐语」降级，脚本不会
    死等（每个请求都有硬时限，同一兜底连不上时本次运行自动跳过）。想抓全，
    在同一终端先开本地代理再跑（端口按你的代理软件实际值）：

    ```cmd
    set HTTPS_PROXY=http://127.0.0.1:7897
    set HTTP_PROXY=http://127.0.0.1:7897
    set NO_PROXY=dashscope.aliyuncs.com,aliyuncs.com,aibase.com,chinaz.com
    ```

    **`NO_PROXY` 必须一起设**：千问是国内服务，被代理中转会打断 SSL
    （日志出现 `text api failed … SSLError`），导读退化为空白；aibase 与
    插图 CDN 同属国内，走代理也会 SSL 失败、插图整批抓空（脚本已内置
    直连重试兜底）。代理开启后境外页面与 r.jina.ai 可达，境外条目也能
    取图、生成导读；浏览器打不开原文页不影响脚本（脚本不依赖你的浏
    览器，直连被拒时由 r.jina.ai 在它自己的服务器上取回正文和图片链
    接）。
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
