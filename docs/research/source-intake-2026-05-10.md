# AI News Radar Source Intake Research — 2026-05-10

> Status: research report only. No source fetcher, OPML, workflow, or data snapshot implementation has been changed.
>
> Repo: `/Users/carl/Downloads/ai-news-radar`

## 0. Baseline

Current AI News Radar already has a two-layer source model:

- **Default layer**: curated public AI/tech signal view for ordinary readers.
- **Advanced layer**: OPML, source health, GitHub Actions secrets, AgentMail/email, optional adapters, and maintainer controls.

Current built-in public coverage, from `docs/SOURCE_COVERAGE.md` and `data/source-status.json`:

- Official AI sources: OpenAI News, Anthropic News, Google DeepMind, Google AI Blog, Hugging Face Blog, GitHub AI & ML, GitHub Changelog.
- Curated/public aggregators: Follow Builders, AI Breakfast, TechURLs, Buzzing, Info Flow, BestBlogs, Zeli, AI HubToday, AIbase, NewsNow.
- OPML customization: supported through `feeds/follow.opml` / `FOLLOW_OPML_B64`.
- Private/fragile surfaces such as X, WeChat, private email, cookies, and browser automation should not become public defaults unless explicitly accepted.

Latest local `data/source-status.json` snapshot shows 12/12 successful sites and no failed or zero-item sites. This research should therefore focus on **adding source quality**, not merely adding volume.

## 1. Target: ClawHub `flowbywind/llm-daily-digest`

Source:

- Page: `https://clawhub.ai/flowbywind/llm-daily-digest`
- Public ZIP endpoint observed by subagent: `https://wry-manatee-359.convex.site/api/v1/download?slug=llm-daily-digest`
- Public files observed: `README.md`, `SKILL.md`, `_meta.json`

### What it is

An **OpenClaw Skill**, not a standalone public RSS/JSON feed. It is designed to let an OpenClaw agent produce a Chinese Markdown digest for LLM/AI news.

It exposes a rich source list, prompt/workflow logic, output format, and cron recommendation, but the output is local Markdown rather than a stable public data feed.

### How it collects news

Observed workflow:

- Default time window: past 24 hours, Asia/Shanghai / UTC+8 oriented.
- Parallel collection across a source list.
- Skips failed sources rather than failing the whole digest.
- Cross-source dedupe, preferring the most authoritative original URL.
- Classifies and scores importance before producing a Chinese digest.
- X/Twitter is treated as optional: use API if available, fallback to search if possible, otherwise skip.

### What it outputs

Local Markdown digest, not a public feed:

```text
~/.openclaw/workspace/digests/YYYY-MM-DD.md
```

It also recommends maintaining an index file and configuring an OpenClaw cron job separately.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI News | `https://openai.com/news/`, `https://openai.com/index/` | official page/RSS candidate | likely stable | no | low | built-in official source, already mostly covered |
| Anthropic News/Research | `https://www.anthropic.com/news`, `https://www.anthropic.com/research` | official page | stable | no | low | built-in official source, already covered |
| Google AI/Gemini/DeepMind | `https://blog.google/technology/ai/`, `https://blog.google/products/gemini/`, `https://deepmind.google/discover/blog/`, `https://ai.google.dev/` | official pages/RSS | mixed | no | medium | split stable official feeds/pages; avoid broad consumer noise |
| Meta AI | `https://ai.meta.com/blog/`, `https://ai.meta.com/research/publications/` | official page | mixed | no | low-medium | evaluate as built-in official candidate |
| xAI News | `https://x.ai/news` | official page | probe: 200 HTML | no | low-medium | focused fetcher watchlist |
| Mistral News | `https://mistral.ai/news` | official page | probe: 200 HTML | no | low | focused fetcher watchlist |
| DeepSeek | `https://api-docs.deepseek.com/news/`, `https://www.deepseek.com/`, `https://huggingface.co/deepseek-ai`, `https://github.com/deepseek-ai` | official/docs/HF/GitHub | mixed | no | medium | official/HF/GitHub route; `api-docs.../news/` probed 404 and needs URL correction |
| Qwen | `https://qwenlm.github.io/blog/`, `https://github.com/QwenLM`, `https://huggingface.co/Qwen` | official blog/GitHub/HF | blog page probe: 200 HTML | no | low-medium | built-in/watchlist; likely high value |
| GitHub Trending | `https://github.com/trending` | public page | unstable ordering | no | high | advanced/watchlist, not public default |
| arXiv cs.CL / cs.AI / cs.LG / cs.CV | `https://arxiv.org/list/cs.CL/recent` etc. | public lists | stable | no | high without filter | optional focused fetcher with strong AI/topic filter |
| Hugging Face Models/Papers | `https://huggingface.co/models?sort=trending`, `https://huggingface.co/papers` | public pages | mixed | no | medium | already partly covered by official HF feed; papers/models need careful dedupe |
| Hacker News | `https://news.ycombinator.com/` | public API/page | stable | no | medium | trend/discussion signal, not fact source |
| X/Twitter accounts | `@OpenAI`, `@AnthropicAI`, `@GoogleDeepMind`, `@deepseek_ai`, `@Alibaba_Qwen`, etc. | social | API-dependent | yes if official API | high | advanced/secret-backed only |
| Chinese AI media | `https://www.jiqizhixin.com/`, `https://www.qbitai.com/` | media pages/RSS candidates | mixed | no | medium | OPML/watchlist; avoid fragile scraping |

### Integration method to borrow

- Keep official one-source-of-truth URLs above media/social copies.
- Record empty-but-checked sources in source status rather than silently hiding them.
- Treat X as advanced, not default.
- Report scan count / raw item count / selected item count.

### Preliminary decision

`watchlist` / **partial reuse**. Do not consume the Skill directly because it has no public feed. Reuse its official-source inventory and quality rules.

## 2. Target: ClawHub `aizain/ai-news-zh`

Source:

- Page: `https://clawhub.ai/aizain/ai-news-zh`
- Public ZIP endpoint observed by subagent: `https://wry-manatee-359.convex.site/api/v1/download?slug=ai-news-zh`
- Public files observed: `SKILL.md`, `sources.md`, `format.md`, `_meta.json`

### What it is

An **OpenClaw Skill** for generating a Chinese AI morning brief from English technology/AI news sources. It is not a public news feed or standalone website.

### How it collects news

- Requires `web_fetch`; optionally uses `web_search` and message/push tools.
- Fetches sources, extracts title/summary/source/time, filters AI relevance, dedupes, translates to Chinese, classifies, and ranks top 8–12 items.
- Pushes text to channels such as Feishu/Telegram/Discord if configured.

### What it outputs

A formatted Chinese daily brief, for example:

```markdown
🤖 AI 早报 | YYYY.MM.DD（weekday）
...
📌 一句话点评：...
```

No public RSS/JSON output was found.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| Wired AI RSS | `https://www.wired.com/feed/tag/ai/latest/rss` | RSS | probe: 200 XML, 10 items, recent dates | no | medium | `example-opml` or built-in media supplement |
| MIT Technology Review feed | `https://www.technologyreview.com/feed/` | RSS | probe: local SSL EOF; needs retry/alternate fetch | no | medium | watchlist until fetch reliability confirmed |
| TechCrunch feed | `https://techcrunch.com/feed/` | RSS | probe: 200 RSS, 20 items | no | high | `example-opml` with AI filter; not default without strong filter |
| The Verge AI page | `https://www.theverge.com/ai-artificial-intelligence` | topic page | page, not directly probed as feed | no | medium | focused fetcher/watchlist |
| Anthropic News | `https://www.anthropic.com/news` | official page | stable | no | low | official source, already covered |
| Google AI Blog | `https://blog.google/technology/ai/` | official page/RSS candidate | stable | no | medium | official source candidate |
| Chinese media | 36Kr, QbitAI etc. | pages/RSS candidates | mixed | no | medium-high | OPML/watchlist only if stable public RSS exists |

### Integration method to borrow

- Use it as a lightweight English-media supplement list.
- Do not import the push channel workflow into AI News Radar public default.
- Use strict AI filtering for broad feeds such as TechCrunch and MIT TR.

### Preliminary decision

`example-opml` / **medium priority**. Reuse stable RSS sources as optional examples; do not depend on the Skill runtime.

## 3. Target: GitHub `sansan0/TrendRadar`

Source:

- Repo: `https://github.com/sansan0/TrendRadar`
- Homepage: `https://sansan0.github.io/TrendRadar/`
- Default branch: `master`
- Evidence paths checked: `README.md`, `config/config.yaml`, `.github/workflows/crawler.yml`, `trendradar/crawler/rss/fetcher.py`, `trendradar/report/rss_html.py`

### What it is

A general-purpose AI-assisted trend/public-opinion monitor. It aggregates hot lists and RSS, stores data locally/remotely, filters by keywords or AI interests, and pushes to Feishu/Telegram/DingTalk/email/ntfy/Bark/Slack etc. It also has MCP support.

### How it collects news

- Hot-list platforms in `config/config.yaml`: Toutiao, Baidu, Wallstreetcn, The Paper, Bilibili, CLS, ifeng, Tieba, Weibo, Douyin, Zhihu.
- RSS in `config/config.yaml`: example feeds include Hacker News, Ruanyifeng, Yahoo Finance.
- `trendradar/crawler/rss/fetcher.py` parses RSS/Atom with configured feed list, timeout, proxy options, max age, and max items.
- `.github/workflows/crawler.yml` runs on cron `33 * * * *` and uses GitHub Secrets for push channels and AI API keys.

### What it outputs

- Local SQLite and HTML report under `output/`.
- GitHub Pages-compatible `index.html` / `output/index.html`.
- Push messages to configured channels.
- MCP query surfaces.

No stable public generated JSON/RSS output intended for third-party consumption was identified from the checked paths.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| Hacker News RSS | `https://hnrss.org/frontpage` | RSS | stable | no | medium | already conceptually covered; optional OPML |
| Ruanyifeng Atom | `http://www.ruanyifeng.com/blog/atom.xml` | RSS/Atom | stable but broad | no | high for AI | skip/default; personal OPML only |
| Yahoo Finance RSS | `https://finance.yahoo.com/news/rssindex` | RSS | stable but finance | no | high | skip for AI News Radar default |
| NewsNow dependency | `https://github.com/ourongxing/newsnow` / NewsNow APIs | aggregator | mixed | no | medium | AI News Radar already has its own NewsNow fetcher |
| Chinese hot-list platforms | Weibo/Douyin/Zhihu/etc. | hot list | dynamic/fragile | often no | high | skip/default; not a public AI source |

### Integration method to borrow

- RSS status accounting and max-age handling are useful patterns.
- The timeline/schedule model is useful for a full product, but too heavy for AI News Radar’s current simple GitHub Actions flow.
- Do not import broad hot-list sources into the public default.

### Preliminary decision

`skip` for source import; `watchlist` for operational patterns. TrendRadar is a broad trend monitor, not a high-signal AI source directory.

## 4. Target: GitHub `alternbits/awesome-ai-newsletters`

Source:

- Repo: `https://github.com/alternbits/awesome-ai-newsletters`
- Default branch: `main`
- Evidence path checked: `README.md`

### What it is

A curated directory of AI-related newsletters. It does not run a pipeline and does not publish a generated feed. It is useful as a discovery list only.

### How it collects news

No code pipeline was found. The repo is a manually curated Markdown list.

### What it outputs

Only README list entries. No JSON/RSS/Atom output.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| AI For Developers | `https://aifordevelopers.substack.com/feed` | Substack RSS | probe: 200 XML, 9 items, latest 2026-04-28 | no | medium | `example-opml` for developer-focused readers |
| AI Breakfast | `https://aibreakfast.beehiiv.com/` | Beehiiv/archive | already has AI News Radar fetcher | no | medium | already covered; do not duplicate |
| BuzzRobot | `https://buzzrobot.substack.com/feed` | Substack RSS | probe: 200 XML, 20 items, latest 2026-04-02 | no | low-medium, slower cadence | `example-opml` or watchlist |
| True Positive Weekly | `https://aiweekly.substack.com/feed` | Substack RSS | probe: 200 XML, 20 items, latest 2026-05-07 | no | low-medium | `example-opml`; ML/AI research weekly |
| AI Evaluation Substack | `https://aievaluation.substack.com/feed` | Substack RSS | probe: 200 XML, 8 items, latest 2026-04-24 | no | low, narrow | `example-opml` / specialist watchlist |
| Data Elixir | `https://dataelixir.com/newsletters/` | archive | not probed as feed | no | medium, broader DS | watchlist |
| Turing Post | `https://www.turingpost.com/` | newsletter/site | not probed as feed | no | medium | watchlist |
| Import AI | Mailchimp subscribe URL | newsletter signup | no public feed identified | no | low but hard to ingest | skip unless public archive/feed found |

### Integration method to borrow

- Treat the repo as a shortlist discovery source, not as an upstream feed.
- Prefer Substack feeds that expose `/feed` and have stable timestamps.
- Do not add the whole list; choose a small set for OPML examples after signal review.

### Preliminary decision

`example-opml` for a small vetted subset. No built-in default until source quality is reviewed against current AI News Radar coverage.

## 5. Target: GitHub `Thysrael/Horizon`

Source:

- Repo: `https://github.com/Thysrael/Horizon`
- Homepage: `https://www.horizon1123.top`
- GitHub Pages/demo feed: `https://thysrael.github.io/Horizon/`
- Evidence paths checked: `README_zh.md`, `README.md`, `data/config.example.json`, `.env.example`, `.github/workflows/daily-summary.yml`, `docs/feed-zh.xml`, `src/scrapers/rss.py`, `src/scrapers/hackernews.py`

### What it is

An AI-powered personal news radar that generates bilingual daily briefings. It supports RSS/Atom, Hacker News, Reddit, Telegram, Twitter/X, GitHub events/releases, AI scoring, dedupe, enrichment, GitHub Pages, email, webhook delivery, and MCP.

### How it collects news

- Config-driven through `data/config.json`.
- Example sources: GitHub user events/repo releases, Hacker News top stories, RSS feeds, Reddit, Twitter.
- RSS scraper uses `httpx` + `feedparser`, expands environment variables in feed URLs, and uses parsed `published/updated/created` timestamps.
- HN scraper uses Firebase API and fetches top comments.
- `.github/workflows/daily-summary.yml` currently exposes `workflow_dispatch`; schedule is commented out. It runs `uv run horizon --hours 48` and deploys docs to `gh-pages`.
- Secrets: at least one LLM API key for scoring/summarization; optional `GITHUB_TOKEN`, `APIFY_TOKEN` for Twitter scraping, webhook/email secrets.

### What it outputs

- Markdown summaries under `data/summaries/`.
- GitHub Pages daily site.
- Atom feeds from docs templates:
  - `https://thysrael.github.io/Horizon/feed-zh.xml` probe: 200 XML, 6 entries.
  - `https://thysrael.github.io/Horizon/feed-en.xml` probe: 200 XML, 5 entries.
- Email/webhook/MCP outputs if configured.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| Horizon zh feed | `https://thysrael.github.io/Horizon/feed-zh.xml` | public Atom feed | probe: 200 XML, 6 entries, recent | no | medium | `watchlist` / public generated feed fetcher candidate |
| Horizon en feed | `https://thysrael.github.io/Horizon/feed-en.xml` | public Atom feed | probe: 200 XML, 5 entries, recent | no | medium | `watchlist` / public generated feed fetcher candidate |
| Simon Willison Atom | `https://simonwillison.net/atom/everything/` | RSS/Atom | probe: 200 XML, 30 entries | no | medium | example OPML; high signal for LLM/dev tools but personal-blog scoped |
| Hacker News Firebase API | `https://hacker-news.firebaseio.com/v0/topstories.json` | public API | stable | no | medium-high | optional discussion/trend signal; not source of record |
| GitHub releases/events | GitHub API/Atom | public with rate limits | stable | optional token | medium | use GitHub Atom/API for specific projects only |
| Twitter/X via Apify | configured through `APIFY_TOKEN` | secret-backed bridge | depends | yes | high | advanced/private only |

### Integration method to borrow

- Public generated Atom feeds are the most directly reusable artifact.
- HN comment enrichment is useful as a secondary signal, not a default fact source.
- Config wizard/source personalization ideas are product inspiration, not an immediate AI News Radar default.

### Preliminary decision

`watchlist`. The public Atom feeds are fetchable and recent, but they are summaries generated by another AI pipeline, not primary sources. Use only if the goal is breadth from peer digests; otherwise prefer original sources.

## 6. Target: GitHub `SuYxh/ai-news-aggregator`

Source:

- Repo: `https://github.com/SuYxh/ai-news-aggregator`
- Homepage: `https://suyxh.github.io/ai-news-aggregator/`
- Evidence paths checked: `README.md`, `src/config.ts`, `.github/workflows/update-ai-news.yml`, `data/source-status.json`, `data/opml-feeds.json`, `feeds/follow.example.opml`, `src/fetchers/newsnow.ts`, `src/fetchers/opml-rss.ts`, `src/fetchers/wechat-rss.ts`

### What it is

A TypeScript AI news aggregator with GitHub Actions + GitHub Pages output. It explicitly overlaps with AI News Radar: 14 professional platforms, 70+ RSS subscriptions, 52 WeChat public accounts, AI/tech filtering, bilingual title handling, JSON snapshots, and OPML support.

### How it collects news

- GitHub Actions schedule: `.github/workflows/update-ai-news.yml` runs `cron: "0 */2 * * *"`.
- Optional private OPML via `FOLLOW_OPML_B64` secret.
- Produces data snapshots then deploys to Pages.
- Fetchers include NewsNow, OPML RSS, WeChat RSS, and others.
- `src/config.ts` replaces/blocks fragile RSSHub-like URLs and defines broad AI keyword filtering.
- `src/fetchers/opml-rss.ts` parses OPML and records per-feed status.
- `src/fetchers/wechat-rss.ts` uses third-party WeChat-to-RSS bridges such as `decemberpei.cyou/rssbox/...`.

### What it outputs

Public JSON snapshots:

- `https://suyxh.github.io/ai-news-aggregator/data/latest-24h.json`
- `https://suyxh.github.io/ai-news-aggregator/data/source-status.json`
- `https://suyxh.github.io/ai-news-aggregator/data/opml-feeds.json`

Probe result for `latest-24h.json`:

- status: 200 JSON
- `generated_at`: `2026-05-10T05:29:52.953Z`
- `total_items`: 615
- `total_items_raw`: 5573
- `site_count`: 14
- `source_count`: 113

Probe result for `source-status.json`:

- status: 200 JSON
- `sites`: 14

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| SuYxh latest snapshot | `https://suyxh.github.io/ai-news-aggregator/data/latest-24h.json` | public generated JSON | probe: 200 JSON, recent | no | medium | public generated feed fetcher candidate |
| SuYxh source status | `https://suyxh.github.io/ai-news-aggregator/data/source-status.json` | public status JSON | probe: 200 JSON | no | low | useful health comparison/reference |
| SuYxh OPML feed list | `https://suyxh.github.io/ai-news-aggregator/data/opml-feeds.json` | public source list JSON | probe via raw repo: 7 categories | no | medium-high | source candidate inventory, not default import |
| QbitAI RSS | `https://www.qbitai.com/feed` | RSS | listed in OPML JSON | no | medium | example OPML/watchlist |
| 宝玉 feed | `https://baoyu.io/feed.xml` | RSS | listed | no | medium | example OPML, user-relevant Chinese AI signal |
| Meituan Tech | `https://tech.meituan.com/feed/` | RSS | listed | no | high for AI | skip/default; personal OPML only |
| X bridge feeds | `https://api.xgo.ing/rss/user/...` | third-party X RSS bridge | bridge-dependent | no direct secret | high/fragile | advanced/watchlist, not default |
| WeChat RSS bridge | `https://decemberpei.cyou/rssbox/wechat-*.xml` | third-party WeChat RSS | bridge-dependent | no direct secret | high/fragile | advanced/watchlist only |
| NewsNow API | `https://newsnow.busiyi.world/api/...` | public aggregator API | mixed | no | medium | AI News Radar already has NewsNow fetcher; compare only |

### Integration method to borrow

- Its public JSON output is a directly consumable upstream artifact if we want a peer-aggregator source.
- Its `opml-feeds.json` is valuable as a candidate inventory, but many entries are X bridges or broad Chinese media and should not be bulk imported.
- Its OPML feed-status model aligns with AI News Radar’s source health design.

### Preliminary decision

`watchlist` / possible `public-generated-feed-fetcher`. This is the most directly reusable GitHub target because it publishes JSON snapshots. Add only after deciding whether AI News Radar should ingest a peer aggregator, and after dedupe/noise tests.

## 7. Target: GitHub `kevinho/clawfeed`

Source:

- Repo: `https://github.com/kevinho/clawfeed`
- Homepage: `https://clawfeed.kevinhe.io`
- Evidence paths checked: `README.md`, `.env.example`, `package.json`, `templates/digest-prompt.md`, `docs/prd/source-personalization.md`, `migrations/003_sources.sql`, `migrations/004_feed.sql`

### What it is

An AI-powered news digest and web dashboard that supports Twitter/RSS/Hacker News/Reddit/GitHub Trending/website/custom API/digest feeds. It can run standalone or as an OpenClaw/Zylos skill.

### How it collects news

README lists source types:

- `twitter_feed`
- `twitter_list`
- `rss`
- `hackernews`
- `reddit`
- `github_trending`
- `website`
- `digest_feed`
- `custom_api`

Its PRD indicates the product is heavily Twitter-centered: production analysis cited roughly 90% Twitter sources, with `@karpathy` and other X accounts among top subscriptions.

Secrets/config:

- Optional Google OAuth for multi-user auth.
- Optional `API_KEY` for digest creation/config writes.
- Optional Lark webhook for feedback.
- Twitter support likely requires API/bridge/OAuth/browser-style infrastructure depending on mode.

### What it outputs

README exposes feed endpoints for a user digest:

- `/feed/:slug` HTML
- `/feed/:slug.json` JSON Feed
- `/feed/:slug.rss` RSS

However, no default public slug/source list suitable for direct ingestion was identified in the checked files. The live site may expose user-specific feeds, but the repo itself is more a product/service than a ready source list.

### Candidate sources found

| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |
| ClawFeed user digest RSS/JSON | `https://clawfeed.kevinhe.io/feed/:slug.rss` / `.json` | public generated feed if slug known | depends on slug | no for public feeds | medium | watchlist; needs concrete slug |
| Twitter-heavy source packs | source packs in product | social source bundles | depends | maybe | high | advanced/private only |
| RSS source type | user-configured RSS | RSS | depends | no | mixed | pattern only |
| raw_items pipeline design | docs/PRD | architecture | n/a | n/a | n/a | borrow health/dedupe/source-personalization ideas |

### Integration method to borrow

- Source pack concept is useful for packaging optional source bundles.
- `/feed/:slug.rss` and `/feed/:slug.json` are good output interface patterns.
- Do not import its Twitter-centric defaults into AI News Radar public default.

### Preliminary decision

`advanced-adapter` / `watchlist`. Useful product pattern, but not a direct source until a concrete public digest slug is selected and probed.

## Consolidated source matrix

| source_name | source_url | upstream_project | source_type | integration_route | default_suitability | timestamp_quality | noise_risk | maintenance_risk | decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Wired AI RSS | `https://www.wired.com/feed/tag/ai/latest/rss` | `ai-news-zh` | RSS | OPML or built-in media fetch | medium | high | medium | low | `example-opml` |
| TechCrunch RSS | `https://techcrunch.com/feed/` | `ai-news-zh` | RSS | OPML with AI filter | low-medium | high | high | low | `example-opml` with filter |
| MIT TR RSS | `https://www.technologyreview.com/feed/` | `ai-news-zh` | RSS | retry/probe first | medium | unknown locally | medium | medium | `watchlist` |
| Mistral News | `https://mistral.ai/news` | `llm-daily-digest` | official page | focused fetcher | medium-high | unknown from page | low | medium | `watchlist` |
| xAI News | `https://x.ai/news` | `llm-daily-digest` | official page | focused fetcher | medium | unknown from page | low-medium | medium | `watchlist` |
| Qwen Blog | `https://qwenlm.github.io/blog/` | `llm-daily-digest` | official blog | focused fetcher / feed discovery | high | unknown from page | low-medium | medium | `watchlist` |
| Horizon zh Atom | `https://thysrael.github.io/Horizon/feed-zh.xml` | Horizon | public generated Atom | generated feed fetcher | low-medium | high | medium | low | `watchlist` |
| Horizon en Atom | `https://thysrael.github.io/Horizon/feed-en.xml` | Horizon | public generated Atom | generated feed fetcher | low-medium | high | medium | low | `watchlist` |
| SuYxh latest JSON | `https://suyxh.github.io/ai-news-aggregator/data/latest-24h.json` | SuYxh aggregator | public generated JSON | generated feed fetcher | medium | high | medium | medium | `watchlist` / candidate fetcher |
| SuYxh OPML source list | `https://suyxh.github.io/ai-news-aggregator/data/opml-feeds.json` | SuYxh aggregator | source inventory JSON | candidate inventory only | low as default | mixed | high | medium-high | `watchlist` |
| QbitAI RSS | `https://www.qbitai.com/feed` | SuYxh OPML | RSS | OPML example | medium | needs probe | medium | low-medium | `example-opml` |
| 宝玉 feed | `https://baoyu.io/feed.xml` | SuYxh OPML | RSS | OPML example | medium | needs probe | medium | low | `example-opml` |
| X bridge feeds | `https://api.xgo.ing/rss/user/...` | SuYxh / ClawFeed | bridge RSS | private/advanced only | low | mixed | high | high | `advanced-adapter` / `watchlist` |
| WeChat RSS bridge | `https://decemberpei.cyou/rssbox/wechat-*.xml` | SuYxh | bridge RSS | advanced only | low | mixed | medium-high | high | `advanced-adapter` |
| AI For Developers | `https://aifordevelopers.substack.com/feed` | awesome-ai-newsletters | Substack RSS | OPML example | medium | high | medium | low | `example-opml` |
| BuzzRobot | `https://buzzrobot.substack.com/feed` | awesome-ai-newsletters | Substack RSS | OPML example | low-medium | high, slower cadence | low-medium | low | `watchlist` / `example-opml` |
| True Positive Weekly | `https://aiweekly.substack.com/feed` | awesome-ai-newsletters | Substack RSS | OPML example | medium | high | low-medium | low | `example-opml` |
| AI Evaluation Substack | `https://aievaluation.substack.com/feed` | awesome-ai-newsletters | Substack RSS | OPML example | low-medium, specialist | high | low | low | `example-opml` |
| ClawFeed digest feed | `https://clawfeed.kevinhe.io/feed/:slug.rss` | ClawFeed | generated feed | needs concrete slug | unknown | unknown | medium | medium | `watchlist` |
| TrendRadar hot lists | many broad platforms | TrendRadar | hot-list aggregator | skip | low | mixed | high | high | `skip` |

## Final recommendation

### Add to built-in default now

None yet. This report intentionally stops before implementation. The strongest eventual candidates need one more source-quality pass:

1. Qwen official blog / feed discovery.
2. Mistral official news.
3. xAI official news.
4. Possibly SuYxh public JSON as a peer-aggregator fetcher, if dedupe/noise is acceptable.

### Add to `follow.example.opml` as examples

Good candidates after one more quick probe and review:

- Wired AI RSS — `https://www.wired.com/feed/tag/ai/latest/rss`
- AI For Developers — `https://aifordevelopers.substack.com/feed`
- True Positive Weekly — `https://aiweekly.substack.com/feed`
- AI Evaluation Substack — `https://aievaluation.substack.com/feed`
- BuzzRobot — `https://buzzrobot.substack.com/feed`
- QbitAI — `https://www.qbitai.com/feed`
- 宝玉 — `https://baoyu.io/feed.xml`

### Keep as advanced/private adapter

- X/Twitter accounts and third-party X RSS bridges.
- WeChat RSS bridges.
- ClawFeed user-specific source packs or Twitter-heavy packs.
- Horizon/SuYxh peer-generated feeds if the goal is optional breadth rather than first-party signals.

### Skip for now

- TrendRadar broad hot-list defaults.
- Finance/general-news feeds such as Yahoo Finance.
- Broad Chinese hot-list/social platforms unless the user explicitly wants a non-AI trend monitor.
- Newsletter signup pages without public archive/feed.

## Suggested next implementation tracks

Pick one narrow track next:

1. **Low-risk OPML examples only**: add 5–7 vetted RSS/Substack feeds to `feeds/follow.example.opml`, update `docs/SOURCE_COVERAGE.md`, no Python/TS fetcher changes.
2. **Official-source fetchers**: add one or two official pages such as Qwen/Mistral/xAI after feed/page structure probing.
3. **Peer generated feed fetcher**: add a single fetcher for SuYxh `latest-24h.json`, with dedupe/noise guardrails.
4. **Advanced-source docs**: document X/WeChat/ClawFeed/Horizon as optional advanced routes without enabling them by default.

Recommended first step: **Track 1**. It is low-risk, reversible, and improves user customization without increasing public default noise.
