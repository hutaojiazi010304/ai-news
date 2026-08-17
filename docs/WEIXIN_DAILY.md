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
没装时封面自动退回静态图。

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

1. 更新代码与数据到最新（`data/` 由云端管线生成）：

   ```bash
   git checkout master
   git pull
   ```

2. 设置 key（仅当前终端，勿写入文件）：

   ```cmd
   set DASHSCOPE_API_KEY=你的key
   ```

   PowerShell 用 `$env:DASHSCOPE_API_KEY="你的key"`，macOS/Linux 用
   `export DASHSCOPE_API_KEY=你的key`。

3. 生成推文：

   ```bash
   python scripts/generate_weixin_article.py --data-dir data --output-dir weixin
   ```

   看结束时的汇总日志确认 key 生效：
   `items=20 … generated=… title_mode=llm cover_mode=headline|brand`。
   出现 `title_mode=fallback` 或 `generated=0` 说明 key 没生效（见 FAQ）。

4. 打开 `weixin/index.html` 检查各条导读、原文链接，以及底部辅助区块里的标题/摘要。

5. 提交并推送，Pages 会自动更新预览页：

   ```bash
   git add weixin/
   git commit -m "chore: update weixin article"
   git push
   ```

6. 手机取稿（预览页更新后）：打开
   `https://hutaojiazi010304.github.io/ai-news-radar/weixin/`
   - 长按选中正文 → 复制 → 粘贴进公众号编辑器正文
     （页面底部的灰色「发布辅助信息」区块不要复制进去）
   - 标题、摘要：从页面底部辅助区块复制（与 `weixin/meta.json` 一致）
   - 封面：保存 `weixin/cover.jpg`（或 `.png`），上传为封面图（2.35:1）
   - 「阅读原文」链接：填辅助区块里的地址（= 雷达主页）

## 本地调试

```bash
# 无 key 冒烟（输出到已 gitignore 的 weixin-test/，不碰正式产物）
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin-test

# 只跑流程不写文件
python scripts/generate_weixin_article.py --data-dir data --output-dir weixin --dry-run

# 重新生成静态兜底封面
python scripts/generate_weixin_article.py --make-fallback-cover --assets-dir assets

# 测试（需 pytest）
python -m pytest tests/test_weixin_article.py -q
```

## 常见问题

- **为什么不用 GitHub Actions 生成？** 千问 API key 有 IP 白名单限制，Actions
  runner 调用必然失败，会降级成无 key 版本（模板标题、无千问导读、静态封面），
  还会覆盖本地生成的结果，因此改为本地手动运行。
- **忘了设 key / key 失效？** 汇总日志里 `title_mode=fallback`、`generated=0`，
  产物是降级版；重新设置 key 后再跑一遍同一条命令覆盖即可。
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
