# 微信公众号每日推文（WeChat Daily Article）

每天自动把 `data/daily-brief.json`（伯乐精选）整理成一篇排版好的公众号推文，
生成在线预览页，你从手机复制粘贴进公众号编辑器即可发布。

- 预览页：`https://<你的用户名>.github.io/ai-news-radar/weixin/`
- 出刊时间：每天北京时间约 08:00（UTC 23:53 由 GitHub Actions 触发）
- 实现：`scripts/generate_weixin_article.py` + `.github/workflows/weixin-daily.yml`
- 本功能**不改动**现有抓取/精选管线，只消费它的产物。

## 工作原理

```
data/daily-brief.json（现有管线产物）
        │
        ▼
scripts/generate_weixin_article.py
  ├─ 选条：按 importance_score 降序取前 20 条
  ├─ 每条导读：配了千问 key 时统一由千问生成（120–200字，21天缓存），
  │     仅在生成失败时回退到数据里已有的推荐语；无 key 时复用已有推荐语
  ├─ 每条信息下附原文链接（纯文本 URL）
  ├─ 标题：千问起标题（≤30字）；失败用模板「AI 雷达 · X月X日｜今日精选N条」
  ├─ 摘要：固定模板（≤120字）
  ├─ 封面：三级兜底
  │     A. 头条驱动 qwen-image 生图（2.35:1，裁剪到 1664×708）
  │     B. 头条含负面词或 A 失败 → 品牌模板 prompt 重试
  │     C. 再失败 → 仓库内静态图 assets/weixin-cover-fallback.png
  └─ 渲染：内联样式 HTML（公众号编辑器只认 inline style）
        │
        ▼
weixin/index.html   预览页（每条附原文链接；底部附标题/摘要/阅读原文纯文本，方便手机复制）
weixin/meta.json    title/digest/cover/read_more_url（将来接 API 草稿箱直接消费）
weixin/cover.jpg|png  封面
weixin/reason-cache.json  推荐语缓存（21 天过期）
```

**没有 API key 也能跑**：推荐语留空（复用数据里已有的）、标题走模板、封面用
静态图，始终 exit 0。key 只用于增强，不是必需。

## 一次性配置（只做一次）

### 1. 配置千问 API key（GitHub Secret）

仓库页面 → Settings → Secrets and variables → Actions → **New repository secret**：

- Name：`DASHSCOPE_API_KEY`
- Value：你的阿里云百炼（DashScope）API key

key 只存在于 GitHub Secrets，不会出现在仓库文件里。**不要把 key 写进任何本地
提交的文件或 `.env`**（`.env*` 已被 gitignore）。

### 2. 确认 Pages 已开启

Settings → Pages → Source：Deploy from a branch → Branch: `master` / `(root)`。

### 3.（可选）自定义参数

Settings → Secrets and variables → Actions → **Variables** 页签，按需新建
（都不建就用默认值）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WEIXIN_BRAND_NAME` | `AI 雷达` | 公众号名定了以后改这里 |
| `WEIXIN_RADAR_URL` | `https://hutaojiazi010304.github.io/ai-news-radar/` | 「阅读原文」链接 |
| `WEIXIN_MAX_ITEMS` | `20` | 每期条数 |
| `WEIXIN_TEXT_MODEL` | `qwen3.8-max` | 文本模型 |
| `WEIXIN_IMAGE_MODEL` | `qwen-image-3.0-pro` | 生图模型 |
| `DASHSCOPE_API_BASE_URL` | DashScope 兼容模式地址 | 一般不用改 |
| `WEIXIN_ENABLED` | （开启） | 设为 `0` 临时停刊 |

## 验证（配置完做一次）

Actions → **WeChat Daily Article** → Run workflow（手动触发）。

成功后：

- 仓库出现 `chore: update weixin article` 提交，`weixin/` 下有新文件；
- 手机打开预览页 `…/ai-news-radar/weixin/`，能看到当天推文。

## 每日取稿流程（约 08:00 后）

1. 手机打开 `https://hutaojiazi010304.github.io/ai-news-radar/weixin/`
2. 长按选中正文 → 复制 → 粘贴进公众号编辑器正文
   （页面底部的灰色「发布辅助信息」区块不要复制进去）
3. 标题、摘要：从页面底部辅助区块复制（与 `weixin/meta.json` 一致）
4. 封面：保存 `weixin/cover.jpg`（或 `.png`），上传为封面图（2.35:1）
5. 「阅读原文」链接：填辅助区块里的地址（= 雷达主页）

## 本地调试

```bash
# 无 key 冒烟（不调网络，产物降级生成）
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin

# 只跑流程不写文件
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin --dry-run

# 重新生成静态兜底封面
python scripts/generate_weixin_article.py --make-fallback-cover --assets-dir assets

# 测试（需 pytest）
python -m pytest tests/test_weixin_article.py -q
```

本地想用千问增强时，临时设置环境变量（仅当前终端，勿写入文件）：

```cmd
set DASHSCOPE_API_KEY=你的key
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin
```

## 常见问题

- **Actions 页看不到这个工作流？** 确认 `.github/workflows/weixin-daily.yml`
  已推到仓库（GitHub 只展示实际存在的 workflow 文件）。
- **定时任务没跑？** fork 仓库的 schedule 不会自动触发；必须是自己新建的仓库
  （本项目已按此方式迁移）。另外 GitHub cron 可能有几分钟到一小时的延迟。
- **封面是静态图？** 说明生图失败（key 无效 / 模型名不可用 / 网络），脚本会
  在 stderr 打印原因。可用 Variables 里的 `WEIXIN_IMAGE_MODEL` 换成可用模型。
- **粘贴进编辑器样式丢了？** 正文只用安全内联样式（无 `<a>`/`<img>`/class）；
  如个别样式丢失，属编辑器行为，内容不受影响。
- **原文链接为什么是纯文本、点不了？** 公众号正文不支持外链超链接，编辑器会
  去掉 `<a>` 标签，所以每条信息下方以纯文本给出原文 URL，读者可复制到浏览器打开。
- **想改排版/条数/品牌名？** 排版改 `scripts/generate_weixin_article.py` 的
  `render_article_html`；条数与品牌名用 Variables 即可，无需改代码。
