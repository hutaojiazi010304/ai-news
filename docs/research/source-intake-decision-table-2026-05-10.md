# AI News Radar Source Intake Decision Table — 2026-05-10

> 用途：给 Carl 逐类确认候选源是否进入下一步收录。
>
> 来源：`docs/research/source-intake-2026-05-10.md`
>
> 当前状态：分类已由 Carl 确认；第一批低风险 `example-opml` 已收录到 `feeds/follow.example.opml`。实现层仍未改 fetcher、不改 workflow。

## 路线说明

| 建议路线 | 含义 | 下一步动作 |
| --- | --- | --- |
| `built-in-official-candidate` | 值得考虑进入默认内置的一手官方源 | 先补一次结构/时间戳探测，再写 focused fetcher 或加入官方源配置 |
| `example-opml` | 适合作为用户自定义信息源示例，不进默认公共流 | 加入 `feeds/follow.example.opml`，并在文档说明适用场景 |
| `public-generated-feed-candidate` | 其他项目已经公开生成 JSON/RSS/Atom，可作为上游聚合源 | 先做 dedupe/noise 测试，再决定是否写 fetcher |
| `watchlist` | 有价值但证据不足、结构未稳、或还需要再探测 | 暂不收录，补探测后再定 |
| `advanced-private` | 需要 token、OAuth、私有配置，或依赖脆弱桥接 | 只进高级/私有路线，不进默认公共流 |
| `already-covered` | 已被 AI News Radar 现有默认源覆盖 | 不重复收录，只在文档中标记覆盖关系 |
| `skip` | 与 AI News Radar 默认目标不匹配，或噪音/维护风险过高 | 不收录 |

---

## A. `built-in-official-candidate`：默认内置官方源候选

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Meta AI Blog | `https://ai.meta.com/blog/` | official page | mixed，需复核页面结构 | 否 | 低-中 | `built-in-official-candidate` | 待确认 |
| Meta AI Research/Publications | `https://ai.meta.com/research/publications/` | official page | mixed，论文量可能偏大 | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| Mistral News | `https://mistral.ai/news` | official page | 已探测：200 HTML；需确认条目时间戳 | 否 | 低 | `built-in-official-candidate` | 待确认 |
| xAI News | `https://x.ai/news` | official page | 已探测：200 HTML；需确认条目时间戳 | 否 | 低-中 | `built-in-official-candidate` | 待确认 |
| Qwen Blog | `https://qwenlm.github.io/blog/` | official blog | 已探测：200 HTML；需做 feed discovery | 否 | 低-中 | `built-in-official-candidate` | 待确认 |
| Qwen GitHub | `https://github.com/QwenLM` | GitHub org/repos | GitHub 时间戳稳定，但需限定 repo/release | 否，可选 token 提额 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| Qwen Hugging Face | `https://huggingface.co/Qwen` | HF org/model hub | mixed，模型更新多，需过滤 | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| DeepSeek 官网 | `https://www.deepseek.com/` | official site | mixed，需复核更新页 | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| DeepSeek Hugging Face | `https://huggingface.co/deepseek-ai` | HF org/model hub | mixed，模型更新多，需过滤 | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| DeepSeek GitHub | `https://github.com/deepseek-ai` | GitHub org/repos | GitHub 时间戳稳定，但需限定 repo/release | 否，可选 token 提额 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| Google Gemini Blog | `https://blog.google/products/gemini/` | official page | mixed；消费级更新较多 | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |
| Google AI for Developers | `https://ai.google.dev/` | official dev site/changelog candidate | 需找 changelog/feed | 否 | 中 | `built-in-official-candidate` / `watchlist` | 待确认 |

---

## B. `example-opml`：适合先放进 OPML 示例的候选源

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Wired AI RSS | `https://www.wired.com/feed/tag/ai/latest/rss` | RSS | 已探测：200 XML，10 items，日期新 | 否 | 中 | `example-opml` | 待确认 |
| TechCrunch RSS | `https://techcrunch.com/feed/` | RSS | 已探测：200 RSS，20 items | 否 | 高，需要 AI 过滤 | `example-opml` with filter | 待确认 |
| AI For Developers | `https://aifordevelopers.substack.com/feed` | Substack RSS | 已探测：200 XML，9 items，latest 2026-04-28 | 否 | 中 | `example-opml` | 待确认 |
| True Positive Weekly | `https://aiweekly.substack.com/feed` | Substack RSS | 已探测：200 XML，20 items，latest 2026-05-07 | 否 | 低-中 | `example-opml` | 待确认 |
| AI Evaluation Substack | `https://aievaluation.substack.com/feed` | Substack RSS | 已探测：200 XML，8 items，latest 2026-04-24 | 否 | 低，偏专项 | `example-opml` | 待确认 |
| BuzzRobot | `https://buzzrobot.substack.com/feed` | Substack RSS | 已探测：200 XML，20 items，latest 2026-04-02；频率较慢 | 否 | 低-中 | `example-opml` / `watchlist` | 待确认 |
| QbitAI RSS | `https://www.qbitai.com/feed` | RSS | 尚需再探测 | 否 | 中 | `example-opml` / `watchlist` | 待确认 |
| 宝玉 feed | `https://baoyu.io/feed.xml` | RSS | 尚需再探测 | 否 | 中 | `example-opml` | 待确认 |
| Simon Willison Atom | `https://simonwillison.net/atom/everything/` | Atom/RSS | 已探测：200 XML，30 entries | 否 | 中，个人博客但 LLM/dev tools 信号强 | `example-opml` | 待确认 |
| Hacker News RSS | `https://hnrss.org/frontpage` | RSS | stable | 否 | 中，高热但泛技术 | `example-opml` / optional discussion signal | 待确认 |
| Chinese AI media: 机器之心 | `https://www.jiqizhixin.com/` | media page/RSS candidate | mixed，需找稳定 RSS | 否 | 中 | `example-opml` / `watchlist` | 待确认 |
| Chinese AI media: QbitAI | `https://www.qbitai.com/` | media page/RSS candidate | mixed，RSS 需复核 | 否 | 中 | `example-opml` / `watchlist` | 待确认 |

---

## C. `public-generated-feed-candidate`：公开生成 feed / JSON 候选

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SuYxh latest snapshot | `https://suyxh.github.io/ai-news-aggregator/data/latest-24h.json` | public generated JSON | 已探测：200 JSON，`generated_at=2026-05-10T05:29:52.953Z`，615 items | 否 | 中，可能与现有源重复 | `public-generated-feed-candidate` | 待确认 |
| SuYxh source status | `https://suyxh.github.io/ai-news-aggregator/data/source-status.json` | public status JSON | 已探测：200 JSON，14 sites | 否 | 低；不是内容源 | `public-generated-feed-candidate` / health reference | 待确认 |
| Horizon zh Atom | `https://thysrael.github.io/Horizon/feed-zh.xml` | public generated Atom | 已探测：200 XML，6 entries，recent | 否 | 中，二次 AI 摘要源 | `public-generated-feed-candidate` / `watchlist` | 待确认 |
| Horizon en Atom | `https://thysrael.github.io/Horizon/feed-en.xml` | public generated Atom | 已探测：200 XML，5 entries，recent | 否 | 中，二次 AI 摘要源 | `public-generated-feed-candidate` / `watchlist` | 待确认 |
| ClawFeed user digest RSS | `https://clawfeed.kevinhe.io/feed/:slug.rss` | public generated RSS if slug known | depends on slug；未拿到具体 slug | 否，公开 feed 不需要 | 中 | `public-generated-feed-candidate` / `watchlist` | 待确认 |
| ClawFeed user digest JSON | `https://clawfeed.kevinhe.io/feed/:slug.json` | public generated JSON if slug known | depends on slug；未拿到具体 slug | 否，公开 feed 不需要 | 中 | `public-generated-feed-candidate` / `watchlist` | 待确认 |

---

## D. `watchlist`：先不收录，补探测后再定

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MIT Technology Review feed | `https://www.technologyreview.com/feed/` | RSS | 本地探测 SSL EOF；需换方式重试 | 否 | 中 | `watchlist` | 待确认 |
| The Verge AI page | `https://www.theverge.com/ai-artificial-intelligence` | topic page | 未直接探测 feed，需找稳定 RSS/API | 否 | 中 | `watchlist` | 待确认 |
| Data Elixir | `https://dataelixir.com/newsletters/` | newsletter archive | 未探测 feed；偏 data science | 否 | 中 | `watchlist` | 待确认 |
| Turing Post | `https://www.turingpost.com/` | newsletter/site | 未探测 feed | 否 | 中 | `watchlist` | 待确认 |
| Hugging Face Models Trending | `https://huggingface.co/models?sort=trending` | public page | mixed，模型转存噪音多 | 否 | 中-高 | `watchlist` | 待确认 |
| Hugging Face Papers | `https://huggingface.co/papers` | public page | mixed，论文日更 | 否 | 中 | `watchlist` | 待确认 |
| arXiv cs.CL recent | `https://arxiv.org/list/cs.CL/recent` | public list | stable | 否 | 高，需要强过滤 | `watchlist` / focused fetcher | 待确认 |
| arXiv cs.AI recent | `https://arxiv.org/list/cs.AI/recent` | public list | stable | 否 | 高，需要强过滤 | `watchlist` / focused fetcher | 待确认 |
| arXiv cs.LG recent | `https://arxiv.org/list/cs.LG/recent` | public list | stable | 否 | 高，需要强过滤 | `watchlist` / focused fetcher | 待确认 |
| arXiv cs.CV recent | `https://arxiv.org/list/cs.CV/recent` | public list | stable | 否 | 高，需要强过滤 | `watchlist` / focused fetcher | 待确认 |
| GitHub Trending | `https://github.com/trending` | public page | unstable ordering | 否 | 高 | `watchlist` / advanced only | 待确认 |
| GitHub releases/events | GitHub API/Atom | public API/Atom | stable，但需指定 repo/org | 可选 token 提额 | 中 | `watchlist` | 待确认 |
| Hacker News Firebase API | `https://hacker-news.firebaseio.com/v0/topstories.json` | public API | stable | 否 | 中-高，泛技术 | `watchlist` / discussion signal | 待确认 |
| SuYxh OPML feed list | `https://suyxh.github.io/ai-news-aggregator/data/opml-feeds.json` | public source inventory JSON | mixed；7 categories | 否 | 中-高，含大量桥接源 | `watchlist` / inventory only | 待确认 |
| RSS source type in ClawFeed | user-configured RSS | pattern, not concrete source | depends | 否 | mixed | `watchlist` / pattern only | 待确认 |
| raw_items pipeline design in ClawFeed | docs/PRD | architecture pattern | n/a | n/a | n/a | `watchlist` / borrow pattern only | 待确认 |

---

## E. `advanced-private`：高级/私有路线，不进默认公共流

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| X/Twitter official accounts | `@OpenAI`, `@AnthropicAI`, `@GoogleDeepMind`, `@deepseek_ai`, `@Alibaba_Qwen` 等 | social/API | API-dependent | 是，官方 API 需要 token | 高 | `advanced-private` | 待确认 |
| Twitter/X via Apify | configured through `APIFY_TOKEN` | secret-backed bridge | depends | 是 | 高 | `advanced-private` | 待确认 |
| X bridge feeds | `https://api.xgo.ing/rss/user/...` | third-party X RSS bridge | bridge-dependent | 无直接 secret，但桥不稳定 | 高 | `advanced-private` / `watchlist` | 待确认 |
| WeChat RSS bridge | `https://decemberpei.cyou/rssbox/wechat-*.xml` | third-party WeChat RSS bridge | bridge-dependent | 无直接 secret，但桥不稳定 | 中-高 | `advanced-private` | 待确认 |
| ClawFeed Twitter-heavy source packs | source packs in product | social source bundle | depends | 可能需要 OAuth/API/登录态 | 高 | `advanced-private` | 待确认 |
| Email/newsletter inbox routes | private inbox / AgentMail style | email bridge | depends | 是 | 中 | `advanced-private` | 待确认 |

---

## F. `already-covered`：已有覆盖，不重复收录

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenAI News | `https://openai.com/news/`, `https://openai.com/index/` | official page/RSS candidate | likely stable | 否 | 低 | `already-covered` | 待确认 |
| Anthropic News | `https://www.anthropic.com/news` | official page | stable | 否 | 低 | `already-covered` | 待确认 |
| Anthropic Research | `https://www.anthropic.com/research` | official page | stable | 否 | 低 | `already-covered` / maybe expand if missing | 待确认 |
| Google DeepMind Blog | `https://deepmind.google/discover/blog/` | official RSS/page | stable | 否 | 低-中 | `already-covered` | 待确认 |
| Google AI Blog | `https://blog.google/technology/ai/` | official page/RSS candidate | stable | 否 | 中 | `already-covered` / maybe expand | 待确认 |
| Hugging Face Blog | `https://huggingface.co/blog/feed.xml` | RSS | stable | 否 | 中 | `already-covered` | 待确认 |
| GitHub AI & ML / Changelog | GitHub official blog/changelog RSS | RSS | stable | 否 | 中 | `already-covered` | 待确认 |
| AI Breakfast | `https://aibreakfast.beehiiv.com/` | Beehiiv/archive | already has AI News Radar fetcher | 否 | 中 | `already-covered` | 待确认 |
| NewsNow API | `https://newsnow.busiyi.world/api/...` | public aggregator API | mixed | 否 | 中 | `already-covered` / compare only | 待确认 |
| NewsNow project | `https://github.com/ourongxing/newsnow` | aggregator project | mixed | 否 | 中 | `already-covered` / compare only | 待确认 |

---

## G. `skip`：建议不收录

| 候选源 | URL | 类型 | 时间戳情况 | 是否秘密 | 噪音风险 | 建议路线 | Carl 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TrendRadar broad hot lists | many broad platforms | hot-list aggregator | mixed | 否/部分平台动态 | 高 | `skip` | 待确认 |
| Chinese hot-list platforms | Weibo/Douyin/Zhihu/etc. | hot list/social | dynamic/fragile | often no | 高 | `skip` | 待确认 |
| Ruanyifeng Atom | `http://www.ruanyifeng.com/blog/atom.xml` | RSS/Atom | stable but broad | 否 | 高 for AI | `skip` for default; personal OPML only | 待确认 |
| Yahoo Finance RSS | `https://finance.yahoo.com/news/rssindex` | RSS | stable but finance | 否 | 高 | `skip` | 待确认 |
| Meituan Tech | `https://tech.meituan.com/feed/` | RSS | listed by upstream | 否 | 高 for AI | `skip` for default; personal OPML only | 待确认 |
| Import AI signup | Mailchimp subscribe URL | newsletter signup | no public feed identified | 否 | 低，但难以 ingest | `skip` unless public archive/feed found | 待确认 |

---

## 我建议 Carl 先确认的 4 个关键分类

1. **是否同意先做 `example-opml`**：Wired、AI For Developers、True Positive Weekly、AI Evaluation、BuzzRobot、QbitAI、宝玉、Simon Willison。
2. **是否同意官方源候选进入下一轮探测**：Qwen、Mistral、xAI、Meta AI、DeepSeek。
3. **是否要接入 peer aggregator**：SuYxh `latest-24h.json`、Horizon Atom。这个会明显增加覆盖，但也会带来二次聚合和重复项。
4. **是否明确把 X/WeChat 桥接源放到 advanced/private**：不进默认公共流，只作为高级玩法记录。
