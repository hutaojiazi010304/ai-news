# AI News Radar 2.0 Source Inventory Summary — 2026-05-10

> 用途：人工验收前，说明当前 AI News Radar 到底收集了多少源，以及这些源以什么方式存在。
>
> 依据：`data/source-status.json`、`scripts/update_news.py`、`feeds/follow.example.opml`、`docs/SOURCE_COVERAGE.md`，以及 2026-05-10 本地 OPML 验证输出。

## 1. 结论

当前可以按三层理解：

1. **默认公共抓取层**：12 个内置 source adapter，当前线上/本地数据快照中 12/12 成功。
2. **OPML 示例/自定义层**：`feeds/follow.example.opml` 已有 10 个公开 RSS/Atom 示例源；本地验证 10/10 成功。它们不会自动进入线上默认流，除非运行时显式传入 OPML。
3. **高级/私有能力层**：AgentMail 邮箱、X API/Follow Builders 类能力、私有 OPML、未来 X/WeChat/peer aggregator 等。默认关闭或通过公开生成文件间接消费，不上传私有数据。

如果按“代码里的 source adapter”数：当前是 **12 个默认 adapter**；启用示例 OPML 后是 **13 个 site 节点**，其中 OPML 节点内部包含 10 条 feed。

如果按“更细颗粒度的具体源入口”数：当前可展示为 **约 30 个入口**：

- 默认官方端点：9 个左右，包括 7 个 RSS/Atom + Anthropic News 页面 + OpenAI Codex Changelog 页面。
- 默认聚合/媒体 adapter：11 个。
- 示例 OPML feed：10 个。

注意：部分聚合器本身还会展开更多上游子源，例如 NewsNow、Follow Builders、Info Flow 等；这里不把它们内部所有上游再逐个计数，避免把“信息量”误说成“可控源数量”。

---

## 2. 默认公共抓取层：12 个 adapter

来自当前 `data/source-status.json` 快照：`generated_at=2026-05-09T11:51:06.929410Z`。

| # | site_id | 展示名 | 存在方式 | 当前状态 | 快照 item_count | 说明 |
| --- | --- | --- | --- | --- | ---: | --- |
| 1 | `official_ai` | Official AI Updates | 内置官方源 adapter | ok | 191 | 官方 RSS/Atom + 官方页面解析 |
| 2 | `aibreakfast` | AI Breakfast | 内置 newsletter/archive adapter | ok | 9 | Beehiiv 公开页经 Jina Reader 读取 |
| 3 | `followbuilders` | Follow Builders | 公开 GitHub-generated JSON | ok | 24 | 读取 `feed-x.json`、`feed-blogs.json`、`feed-podcasts.json` |
| 4 | `techurls` | TechURLs | 内置公开聚合源 adapter | ok | 405 | 公共聚合源 |
| 5 | `buzzing` | Buzzing | 内置公开聚合源 adapter | ok | 744 | 公共聚合源 |
| 6 | `iris` | Info Flow | 内置公开聚合源 adapter | ok | 487 | 公共聚合源 |
| 7 | `bestblogs` | BestBlogs | 内置公开聚合源 adapter | ok | 1 | 公共聚合源 |
| 8 | `zeli` | Zeli | 内置公开聚合源 adapter | ok | 64 | 公共聚合源 |
| 9 | `aihubtoday` | AI HubToday | 内置 AI 聚合源 adapter | ok | 10 | AI 聚合源 |
| 10 | `aibase` | AIbase | 内置 AI 媒体/聚合源 adapter | ok | 20 | AI 聚合源 |
| 11 | `newsnow` | NewsNow | 内置公开聚合源 adapter | ok | 143 | 公共聚合源 |

（原表中的第 8 行热榜聚合源已于 v0.8 删除，历史快照数据见 reports/source-quality/v0.8-audit.md。）

当前默认快照统计：

```text
successful_sites: 12
failed_sites: []
zero_item_sites: []
fetched_raw_items: 5145
items_before_topic_filter: 6932
items_in_24h: 654
rss_opml.enabled: false
agentmail.enabled: false
```

---

## 3. `official_ai` 内部官方源

`official_ai` 在界面和 source status 里是 1 个 adapter，但内部包含多个一手官方端点。

| # | 名称 | URL | 存在方式 |
| --- | --- | --- | --- |
| 1 | OpenAI News | `https://openai.com/news/rss.xml` | 官方 RSS |
| 2 | Google DeepMind | `https://deepmind.google/blog/rss.xml` | 官方 RSS |
| 3 | Google AI Blog | `https://blog.google/innovation-and-ai/technology/ai/rss/` | 官方 RSS |
| 4 | Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | 官方 RSS |
| 5 | GitHub AI & ML | `https://github.blog/ai-and-ml/feed/` | 官方 RSS |
| 6 | GitHub Changelog | `https://github.blog/changelog/feed/` | 官方 RSS |
| 7 | OpenAI Skills | `https://github.com/openai/skills/commits/main.atom` | GitHub Atom |
| 8 | Anthropic News | `https://www.anthropic.com/news` | 官方页面解析 |
| 9 | OpenAI Codex Changelog | `https://developers.openai.com/codex/changelog` | 官方页面解析 |

---

## 4. OPML 示例/自定义层：10 个 feed

`feeds/follow.example.opml` 当前有 10 个公开 RSS/Atom 示例源。它们是给维护者复制到 `feeds/follow.opml` 后启用的，不会默认污染公共首页。

| # | 名称 | URL | 类型 | 存在方式 |
| --- | --- | --- | --- | --- |
| 1 | OpenAI News | `https://openai.com/news/rss.xml` | RSS | OPML 示例；与官方默认源重复，用于教学 |
| 2 | Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | RSS | OPML 示例；与官方默认源重复，用于教学 |
| 3 | Wired AI | `https://www.wired.com/feed/tag/ai/latest/rss` | RSS | OPML 示例 |
| 4 | InfoQ CN | `https://www.infoq.cn/feed` | RSS | OPML 示例，原有示例源 |
| 5 | 宝玉 | `https://baoyu.io/feed.xml` | RSS | OPML 示例 |
| 6 | Simon Willison | `https://simonwillison.net/atom/everything/` | Atom/RSS | OPML 示例 |
| 7 | AI For Developers | `https://aifordevelopers.substack.com/feed` | Substack RSS | OPML 示例 |
| 8 | True Positive Weekly | `https://aiweekly.substack.com/feed` | Substack RSS | OPML 示例 |
| 9 | AI Evaluation | `https://aievaluation.substack.com/feed` | Substack RSS | OPML 示例 |
| 10 | BuzzRobot | `https://buzzrobot.substack.com/feed` | Substack RSS | OPML 示例 |

本地验证命令启用 `feeds/follow.example.opml` 后：

```text
site_nodes: 13
successful_sites: 13
failed_sites: []
zero_item_sites: []
rss_opml.feed_total: 10
rss_opml.ok_feeds: 10
rss_opml.failed_feeds: []
OPML RSS item_count: 1906
```

---

## 5. 高级/私有能力层

这些能力适合在 2.0 里作为“能力展示”，但不应默认上传到线上公共数据。

| 能力 | 当前存在方式 | 默认是否启用 | 是否上传线上 | 建议 2.0 展示方式 |
| --- | --- | --- | --- | --- |
| 私有 OPML/RSS | `feeds/follow.opml` 本地文件或 `FOLLOW_OPML_B64` GitHub Secret | 否 | 否，除非用户自己在 fork 中启用 | 展示“支持私人 OPML”，只展示状态/数量，不展示私有 URL |
| AgentMail 邮箱摘要 | `EMAIL_DIGEST_ENABLED=1` + `AGENTMAIL_API_KEY` + `AGENTMAIL_INBOX_ID` | 否 | 默认不发布；只有 `EMAIL_DIGEST_PUBLISH=1` 才发布 metadata-only | 展示“支持邮箱订阅源”，默认 `disabled/private` |
| X API 直连 | 未来可用 `X_BEARER_TOKEN` / 官方 API adapter | 否 | 不上传 token，不默认发布原始时间线 | 展示“支持 X API 私有源”，仅展示能力和示例状态 |
| Follow Builders | 当前通过公开 GitHub JSON 间接消费 | 是，作为默认 adapter | 上传的是公开生成 JSON 的结果，不需要本仓库 X token | 展示为“公开生成 feed 模式”成功案例 |
| WeChat/X 第三方桥 | 调研中归为 advanced/private | 否 | 否 | 展示为“高级桥接能力，需要自担稳定性” |
| Peer aggregator | SuYxh JSON、Horizon Atom 等候选 | 否 | 未接入 | 展示为“可扩展方向”，不进默认流 |

---

## 6. 对 2.0 首页/验收页的建议

建议不要把高级源的真实内容放进线上默认数据，而是做一个 **Source Capability / Advanced Sources** 展示区：

1. **Public default sources**：展示 12 个默认 adapter，显示健康状态、item_count、更新时间。
2. **Example OPML sources**：展示 10 个示例 feed，标记“template only / optional”。
3. **Private advanced sources**：展示能力卡片，不展示私有内容：
   - X API：`supported, private, disabled by default`
   - Email/AgentMail：`supported, metadata-only, publish disabled by default`
   - Private OPML：`supported via local file or GitHub Secret`
   - WeChat/X bridge：`advanced, unstable, opt-in`
4. **Safety copy**：明确写出：默认公共版不需要 API Key，不上传 token，不提交私有 OPML，不发布邮箱正文。

这符合 Carl 的两个目标：

- **不会上传到线上**：高级源默认只展示 capability/status，不展示 raw data。
- **不会产生额外开销**：高级源默认 disabled，不拉取、不调用 API、不消耗额度。
