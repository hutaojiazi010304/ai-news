# AI News Radar Source Intake Research Plan

> **For Hermes:** Use `ai-news-radar` and `web-access` skills. This is a research-first plan. Do not modify source fetchers, OPML files, workflows, or data snapshots until Carl approves the intake report.

**Goal:** One by one dissect 7 external AI-news projects/assets, extract useful information sources and integration methods, then decide what should be added to AI News Radar.

**Architecture:** Treat each target as an upstream intelligence source, not as code to copy. First identify its source list and ingestion pipeline; then classify each candidate source into built-in default, example OPML, advanced/private adapter, or skip. Prefer stable public RSS/Atom/JSON/GitHub-generated feeds over scraping, login-bound sources, or fragile social bridges.

**Repo:** `/Users/carl/Downloads/ai-news-radar`

**Targets:**

1. `https://clawhub.ai/flowbywind/llm-daily-digest`
2. `https://clawhub.ai/aizain/ai-news-zh`
3. `https://github.com/sansan0/TrendRadar`
4. `https://github.com/alternbits/awesome-ai-newsletters`
5. `https://github.com/Thysrael/Horizon`
6. `https://github.com/SuYxh/ai-news-aggregator`
7. `https://github.com/kevinho/clawfeed`

---

## Non-goals for this round

- Do not add sources to `scripts/update_news.py` yet.
- Do not edit `feeds/follow.example.opml` yet.
- Do not commit private OPML, cookies, tokens, API keys, email bodies, or `.env` values.
- Do not clone another project's secret-backed API flow into AI News Radar by default.
- Do not treat a source as suitable just because another project uses it.
- Do not rely on public RSSHub/X/Nitter-style bridges as default unless the risk is explicitly accepted.

---

## Acceptance criteria

The research is complete when there is a Markdown report containing:

1. A section for each of the 7 targets.
2. For each target:
   - project purpose;
   - source inventory;
   - ingestion methods;
   - output artifacts, if any;
   - credentials/secrets required;
   - update schedule;
   - whether GitHub Actions can fetch it without login;
   - source candidates worth reusing;
   - recommended route for each candidate.
3. A consolidated source matrix:
   - `source_name`
   - `source_url`
   - `upstream_project`
   - `source_type`
   - `integration_route`
   - `default_suitability`
   - `timestamp_quality`
   - `noise_risk`
   - `maintenance_risk`
   - `decision`
4. A final recommendation split into:
   - **Add to built-in default now**
   - **Add to `follow.example.opml` as examples**
   - **Keep as advanced/private adapter**
   - **Skip for now**
5. No implementation is performed until Carl reviews the report.

---

## Research rubric

### Source classes

| Class | Preferred AI News Radar route | Default suitability |
| --- | --- | --- |
| Official RSS/Atom | `OFFICIAL_AI_FEEDS` or OPML | High |
| Official changelog/static page | focused fetcher only if stable | Medium to high |
| Public GitHub-generated JSON/RSS | built-in fetcher reading raw GitHub URL | High if curated/timestamped |
| Newsletter list | OPML examples or curated built-ins | Medium |
| Aggregator HTML/API | custom fetcher only if stable and low-noise | Medium |
| X/Twitter/social bridge | advanced/private only unless centralized feed is stable | Low |
| Email/inbox | advanced/private only | Low |
| WeChat/private platforms | skip or advanced/private only | Low |

### Decision labels

- `default-now`: useful to most public visitors, stable, no secrets, timestamped.
- `example-opml`: useful but personal/optional; safe as an example feed.
- `advanced-adapter`: valuable but needs secrets, rate-limit handling, or private config.
- `watchlist`: promising but unstable or unclear; revisit later.
- `skip`: too noisy, private, duplicated, blocked, or legally/operationally fragile.

---

## Phase 0: Baseline context

**Objective:** Confirm AI News Radar's current boundaries before evaluating external projects.

**Files to read:**

- `docs/SOURCE_COVERAGE.md`
- `skills/ai-news-radar/references/source-intake.md`
- `scripts/update_news.py`, especially current built-in sources and fetcher patterns
- `data/source-status.json`
- `feeds/follow.example.opml`
- `feeds/social-x.example.opml`

**Commands:**

```bash
git status --short --branch
git log --oneline -5
```

**Output:** One short baseline note: what is already covered, what gaps this research should focus on.

---

## Phase 1: Target-by-target dissection

For each target, use the same template. This keeps the long conversation controllable.

### Template per target

```markdown
## Target N: <name>

### What it is
- Purpose:
- Audience:
- Is it a source directory, aggregator, generated feed, workflow, or agent asset?

### How it collects news
- Input files/configs:
- Code path / workflow path:
- Fetching method:
- Schedule:
- Dependencies:
- Required secrets:

### What it outputs
- Public JSON/RSS/Atom/OPML:
- Public GitHub Pages/static files:
- Newsletter/email/output only:
- Timestamp fields:
- Canonical URL fields:
- Dedup/AI-filter fields:

### Candidate sources found
| Candidate | URL | Type | Timestamp | Secret? | Noise risk | Suggested route |
| --- | --- | --- | --- | --- | --- | --- |

### Integration method to borrow
- Directly reusable pattern:
- Pattern to avoid:
- Test/probe required:

### Preliminary decision
- `default-now` / `example-opml` / `advanced-adapter` / `watchlist` / `skip`
- Reason:
```

### Target-specific notes

#### 1. ClawHub: flowbywind / llm-daily-digest

Research focus:
- Is it an agent/skill, workflow, or public digest page?
- Does it expose a source list, prompt, schedule, or output feed?
- Are sources hardcoded in prompt text or config?
- Can its sources be converted to OPML or built-in feeds?
- Does it depend on ClawHub runtime or private credentials?

Expected route if useful:
- Extract source list and prompts.
- Treat direct source URLs as candidates.
- Do not depend on ClawHub runtime unless there is a public artifact.

#### 2. ClawHub: aizain / ai-news-zh

Research focus:
- Chinese AI-news orientation and source coverage.
- Whether it contains useful Chinese-language sources or only prompt logic.
- Whether sources are public RSS/pages or platform/social dependent.

Expected route if useful:
- Strong Chinese public feeds may go to OPML examples or built-in aggregator layer.
- WeChat/private-social dependencies should stay advanced/watchlist.

#### 3. GitHub: sansan0 / TrendRadar

Research focus:
- Repo structure, config files, workflows, output files.
- Whether it generates public feeds or only runs a dashboard.
- Source categories: GitHub trending, Hacker News, Product Hunt, social, RSS, etc.
- Whether it has reusable ranking/dedupe logic.

Expected route if useful:
- Prefer consuming its public generated JSON/RSS if stable.
- If it only provides generic trending, only AI-filtered parts should be considered.

#### 4. GitHub: alternbits / awesome-ai-newsletters

Research focus:
- This is likely a curated newsletter directory; verify rather than assume.
- Extract newsletter names, archive URLs, RSS/Substack/Beehiiv feed availability.
- Determine which are AI-news-digest focused vs generic AI education/marketing.

Expected route if useful:
- Do not add the whole list by default.
- Produce a shortlist of high-signal newsletters.
- Put most into OPML examples; only add built-ins if public archives are stable and low-noise.

#### 5. GitHub: Thysrael / Horizon

Research focus:
- Source inventory and pipeline architecture.
- How it fetches, dedupes, scores, enriches, publishes, and distributes.
- Which sources are public and reusable.
- Which methods require LLM/API/webhook/email/MCP.

Expected route if useful:
- Borrow product/pipeline ideas selectively.
- Reuse public feed/source lists if stable.
- Keep heavy LLM scoring, email/webhook/MCP as future modules, not default promises.

#### 6. GitHub: SuYxh / ai-news-aggregator

Research focus:
- Source configs and fetcher types.
- Whether it has stable public outputs.
- Chinese/English source mix.
- Whether it uses scraping, RSS, APIs, or browser automation.

Expected route if useful:
- Official/public RSS sources can be candidates.
- Scraping-heavy sources should be watchlist unless very stable.

#### 7. GitHub: kevinho / clawfeed

Research focus:
- Whether it is an OpenClaw/ClawHub feed bridge or general feed generator.
- Source declaration format.
- Output format and whether it can generate RSS/JSON/OPML.
- Reusable source-management method for AI News Radar users.

Expected route if useful:
- If it is a feed generation tool, consider it as an advanced workflow or example, not necessarily a default source.
- If it exposes public generated feeds, evaluate those directly.

---

## Phase 2: Parallel execution strategy

Because the 7 targets are independent, split them into three research batches:

### Batch A: ClawHub assets

- `llm-daily-digest`
- `ai-news-zh`

Goal: determine whether ClawHub pages expose source lists/prompt logic and whether anything can be reused without ClawHub runtime.

### Batch B: GitHub code aggregators

- `TrendRadar`
- `Horizon`
- `ai-news-aggregator`
- `clawfeed`

Goal: inspect repos for config/source lists/workflows/public outputs and integration patterns.

### Batch C: Newsletter directory

- `awesome-ai-newsletters`

Goal: extract newsletter candidates and classify by public feed availability and signal quality.

---

## Phase 3: Probe candidate URLs

Only after source candidates are extracted:

1. Probe each candidate with lightweight HTTP checks.
2. Prefer RSS/Atom/JSON headers and parseability.
3. Check whether timestamps exist and are recent.
4. Check whether GitHub Actions can fetch without login.
5. Record failures, redirects, Cloudflare blocks, missing timestamps, or encoding problems.

Suggested command pattern:

```bash
python - <<'PY'
# For each candidate: request URL, record status, content-type, final URL,
# parse RSS/Atom/JSON where possible, count recent items, print compact table.
PY
```

No source should be promoted to built-in default without this probe.

---

## Phase 4: Recommendation and implementation plan

Create a final recommendation report:

```text
docs/research/source-intake-2026-05-10.md
```

Then, only after Carl approves, create an implementation plan for one of these tracks:

### Track 1: Low-risk docs/OPML only

- Add vetted feeds to `feeds/follow.example.opml`.
- Update `docs/SOURCE_COVERAGE.md` with the new optional source classes.
- No Python fetcher changes.

### Track 2: Built-in public feeds

- Add selected official RSS/Atom feeds to `OFFICIAL_AI_FEEDS`.
- Add tests only if helper behavior changes.
- Run full validation and source-only generation.

### Track 3: Public generated feed fetchers

- Add focused `fetch_<source>` functions for stable public JSON/RSS outputs.
- Add parser tests.
- Update source status docs.

### Track 4: Advanced/private adapters

- Document optional adapters or private OPML flow.
- Do not enable by default.
- Require secrets to be absent-safe.

---

## Validation for any later implementation

For README/docs-only changes:

```bash
git diff --check
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/ai-news-radar
```

For source/fetcher changes:

```bash
python -m py_compile scripts/update_news.py
python -m pytest -q
python scripts/update_news.py --output-dir /tmp/ai-news-radar-data --window-hours 24 --archive-days 21
```

Then inspect:

```bash
/tmp/ai-news-radar-data/source-status.json
/tmp/ai-news-radar-data/latest-24h.json
/tmp/ai-news-radar-data/latest-24h-all.json
```

Acceptance signals:

- new source appears with `ok: true`;
- item count is plausible;
- no failed source unless documented;
- AI-focused view is not flooded by off-topic items;
- public default still runs without any API key.

---

## Suggested first execution checkpoint

Stop after producing the research report. Ask Carl to choose one of:

1. Add only OPML examples.
2. Add a small set of built-in official feeds.
3. Add one public generated feed fetcher.
4. Keep some items as advanced/private adapters.

Do not commit implementation before this checkpoint.
