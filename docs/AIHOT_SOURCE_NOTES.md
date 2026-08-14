# AI HOT Source Notes

Date: 2026-06-16

This note records public clues about the sources used by
`https://aihot.virxact.com`. It is a research note for future source planning,
not a confirmed backend source inventory. AI HOT does not appear to publish its
complete private source configuration, so these entries come from public pages:

- `https://aihot.virxact.com`
- `https://aihot.virxact.com/all`
- `https://aihot.virxact.com/submit`
- `https://aihot.virxact.com/agent`
- `https://aihot.virxact.com/about`

## What AI HOT Appears To Do

AI HOT is not a single-feed news site. From the public UI, it appears to combine
RSS feeds, official pages, X accounts, newsletters/blogs, Chinese public-account
content, research sources, and AI-based selection.

The product pattern worth studying:

- Ingest many noisy sources.
- Keep the default page focused on selected items.
- Add Chinese titles, summaries, recommendation reasons, scores, and tags.
- Merge related reports into multi-source hot topics.
- Provide public RSS, API, and Skill/Agent access.

## Publicly Visible Source Types

### Official AI And Company Sources

- OpenAI official updates
- Anthropic Newsroom
- xAI News
- OpenRouter Announcements
- GitHub Blog
- GitHub Releases
- Claude Code GitHub Releases
- Cloudflare Blog

### Media, Newsletters, And Blogs

- IT之家 RSS
- TechCrunch AI
- The Verge AI
- Bloomberg Technology
- The Decoder AI News
- MarkTechPost
- Artificial Intelligence News
- Ars Technica AI
- VentureBeat
- DataGuidance
- Gary Marcus: The Road to AI We Can Trust
- Nathan Lambert: Interconnects
- Tomer Tunguz blog
- Simon Willison
- Berkeley RDI Blog
- LMSYS / Chatbot Arena Blog
- Lilian Weng blog
- Shunyu Yao blog

### Research And Paper Sources

- Hugging Face Blog
- HuggingFace Daily Papers
- arXiv-style paper items, often surfaced through research blogs or daily-paper
  feeds

### X / Twitter Accounts

Public pages show X-origin items from accounts including:

- Kim / `@kimmonismus`
- Satya Nadella
- Rohan Paul
- OpenRouter
- Perplexity
- Anthropic
- Claude / Claude Devs
- OpenAI Developers
- Andrej Karpathy
- Boris Cherny
- BaoYu
- AYi
- 小互
- Berry Xia
- Artificial Analysis
- Testing Catalog
- Elvis Saravia
- KreaAI
- Odyssey
- ViggleAI

Treat this category carefully. Direct X API access should remain optional and
secret-backed unless a stable public generated feed exists.

### Chinese Sources And Public Accounts

Public AI HOT pages show Chinese-source items including:

- 数字生命卡兹克
- 卡尔的AI沃茨
- MiniMax / 稀宇科技
- 月之暗面 / Kimi
- 通义实验室 / 千问
- 智谱 / GLM
- 蚂蚁百灵 / Ling
- IT之家

These may come from RSS, public-account pipelines, manual curation, or internal
adapters. Do not assume they are all available through stable public feeds.

### Communities And Aggregators

- Hacker News popular items, including `buzzing.cc` Chinese-translated hot items
- HuggingFace Daily Papers
- GitHub projects and open-source repositories
- Product and AI-tool updates

## Sources Listed On The Public Source Wall

The `/submit` page includes a public "source wall" of submitted or accepted
sources. This is useful for discovery, but it should not be treated as the full
live backend source list.

- Artificial Intelligence News
- The Verge
- DataGuidance
- Lilian Weng blog
- KreaAI official X
- MarkTechPost
- TechCrunch
- `hermes-desktop` GitHub repository
- Ars Technica
- Odyssey official X
- Shunyu Yao blog
- VentureBeat
- Cloudflare Blog
- ViggleAI official X
- Claude Devs X

## Comparison With AI News Radar

AI News Radar already has some similar building blocks:

- Official AI updates
- OPML/RSS support
- AI HOT feed ingestion
- Follow Builders public feeds
- Buzzing / TechURLs / NewsNow / Zeli aggregators
- AI relevance scoring
- Story merging
- Source health reporting

If we want to move closer to AI HOT's product feel, the highest-value next
steps are probably:

1. Build a stronger selected-items layer instead of showing too much raw feed
   volume.
2. Add a curated Chinese AI source set, especially AI-focused public accounts
   and official Chinese model-company updates.
3. Add a carefully chosen X/account layer through stable public feeds or
   secret-backed adapters.
4. Make multi-source hot-topic clustering more visible in the first screen.
5. Add recommendation reasons and source confidence labels for each selected
   story.

## Cautions

- Public UI source labels are evidence, not a guaranteed backend config.
- Some sources may be private, manually curated, or routed through internal
  adapters.
- X, public-account, and newsletter sources can be fragile or credential-bound.
- Before adding any source, evaluate it with the source rules in
  `docs/SOURCE_COVERAGE.md`.
