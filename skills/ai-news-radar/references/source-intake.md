# Source Intake Reference

Use this when evaluating a new information source for AI News Radar.

## V2 Intake Questions

Before implementation, answer these from the URL, repo, or user's message:

- Who benefits: every public visitor, the maintainer, or one private fork?
- What signal does it add that stronger existing sources do not already cover?
- Does it publish stable timestamps and canonical URLs?
- Can GitHub Actions fetch it without login, cookies, or browser automation?
- Does it need secrets? If yes, can it skip cleanly when secrets are missing?
- Will it flood the Signal view with off-topic items?

Only ask the user when the answer changes default vs advanced routing.

## Decision Order

1. Prefer official RSS/Atom/JSON feeds.
2. Prefer public GitHub-generated feeds over rebuilding another project's crawler.
3. Use OPML for private or user-specific feeds.
4. Before making a source public-default, run the Source Overlap Check against the recent archive.
5. Use a built-in fetcher only when the source improves the public default.
6. Avoid login, cookies, browser automation, private inboxes, and committed secrets.

## Source Classes

| Source class | Preferred integration | Default suitability |
| --- | --- | --- |
| Official RSS/Atom | OPML or built-in official source | High |
| GitHub generated JSON/RSS | Built-in fetcher reading raw GitHub URLs | High if curated and timestamped |
| GitHub releases/commits | Atom feed | Medium to high |
| Public changelog page | Focused `requests` + BeautifulSoup fetcher | Medium |
| Aggregator site | Existing custom fetcher pattern | Medium |
| Newsletter archive | RSS if available; stable archive page otherwise | Medium |
| X/Twitter public timeline | Curated central feed or optional X API adapter | Low as default |
| Email inbox | Do not default; require explicit user-owned bridge/API | Low |
| WeChat/private social | Optional only, bridge-dependent | Low |

## GitHub Project Intake

When a user gives a GitHub repo:

1. Read `README*`, `SKILL.md`, `config/*`, and `.github/workflows/*`.
2. Look for generated outputs: `feed*.json`, `latest*.json`, `rss.xml`,
   `atom.xml`, `state*.json`, or a `gh-pages` branch.
3. Inspect output schema:
   - title/text field
   - canonical URL
   - timestamp (`createdAt`, `publishedAt`, `generatedAt`, `pubDate`)
   - source/person/feed name
   - stable IDs for dedupe
4. Inspect workflow secrets and dependencies:
   - If the repo uses API keys centrally but publishes public feed files, consume
     the feed files.
   - If every user must provide keys, treat it as an optional advanced source.
5. Test raw URLs from GitHub Actions-friendly locations:
   - `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<file>`
   - public GitHub Pages URL if provided
6. Add a fetcher only after a source-only probe succeeds locally.

Treat public generated feed files as the durable path. If the repo has a stable
GitHub Action that commits `feed*.json` using its own API credentials, consume
those public outputs. Do not clone its token flow into this repo unless the user
explicitly asks for a self-hosted variant.

## X/Twitter Rules

Do not depend on public RSSHub/XCancel/Nitter routes as a default source unless
the user explicitly accepts instability. They can work briefly and then time out.

Stable patterns:

- Read a curated central feed that already uses official X API, like
  Follow Builders.
- Add a self-hosted optional X adapter using `X_BEARER_TOKEN`; skip cleanly when
  the token is missing.

For optional X API adapters:

- Store tokens only in GitHub Secrets or environment variables.
- Never print or commit tokens.
- Exclude retweets/replies by default.
- Cap per-account items.
- Record rate-limit or permission failures in `source-status.json`.
- Skip cleanly when `X_BEARER_TOKEN` is missing; the public site must still run.

## Newsletter Rules

Prefer public archive feeds:

- Substack RSS when available.
- Beehiiv/public archive pages when RSS is blocked.
- Jina Reader only when direct fetch is blocked and the archive is public.

Avoid private inbox ingestion as a default feature. Email requires OAuth, IMAP,
App Passwords, forwarding, or third-party bridges and raises privacy concerns.
If email support is requested, document it as an advanced private bridge and keep
the public default independent from mailbox access.

## Built-In Fetcher Pattern

Add focused code to `scripts/update_news.py`:

```python
def fetch_example(session: requests.Session, now: datetime) -> list[RawItem]:
    resp = session.get("https://example.com/feed.json", timeout=20)
    resp.raise_for_status()
    data = resp.json()
    out: list[RawItem] = []
    for item in data.get("items", []):
        published = parse_date_any(item.get("publishedAt"), now)
        if not published:
            continue
        out.append(
            RawItem(
                site_id="example",
                site_name="Example",
                source=item.get("source") or "Example",
                title=maybe_fix_mojibake(item["title"]),
                url=item["url"],
                published_at=published,
                meta={},
            )
        )
    if not out:
        raise ValueError("No Example items parsed")
    return out
```

Then register it in `collect_all`, update docs, and add tests for parser behavior.

## Source Overlap Check

Before promoting a candidate RSS/Atom source into the public default layer, run a recent-overlap report:

```bash
python scripts/evaluate_source_overlap.py \
  --source-url https://example.com/feed.xml \
  --source-name "Example Source" \
  --site-id example_candidate \
  --baseline data/archive.json \
  --lookback-days 7 \
  --output reports/source-intake/example-overlap.json
```

Interpretation:

- `< 35%` hard duplicate rate: usually `accept_default`.
- `35%–65%`: keep as `watchlist` or OPML advanced source first.
- `>= 65%`: usually `skip_duplicate` unless the source is faster, more canonical, or has unusually valuable unique items.
- `< 5` recent candidate items: treat as too small and keep on `watchlist`.

The report is advisory. Do not auto-delete existing sources or auto-promote a candidate solely from the score. Story-level merge / clustering is planned for a later version, not part of v0.3.0.

## Validation Checklist

Run:

```bash
python -m py_compile scripts/update_news.py
pytest -q
python scripts/update_news.py --output-dir /tmp/ai-news-radar-data --window-hours 24 --archive-days 21
```

Inspect `/tmp/ai-news-radar-data/source-status.json`:

- source appears with `ok: true`
- `failed_sites` is empty or the failure is expected and documented
- item count is plausible
- AI-focused view is not flooded with off-topic items
