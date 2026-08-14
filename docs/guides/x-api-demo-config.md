# X API Demo Configuration — Builder Account Profile

Date: 2026-05-11
Scope: a safe, low-cost demo profile for showing AI News Radar's optional X API source. Do not use this as a public default.

## Goal

Use official X API recent search to pull a small number of public posts from a short builder account list, while keeping the default public site free of secrets and paid usage.

This demo is intentionally smaller than Follow Builders:

- Follow Builders tracks 25 X accounts centrally and publishes `feed-x.json` once per day.
- This demo starts with 5 accounts and at most 10 returned posts per run.
- Public users should still consume Follow Builders' public JSON feed by default instead of needing their own X API token.

## Demo account set

Start with these 5 accounts:

```text
karpathy
swyx
amasad
sama
garrytan
```

Recommended query:

```bash
export X_API_QUERY='(from:karpathy OR from:swyx OR from:amasad OR from:sama OR from:garrytan) -is:retweet -is:reply'
```

Why this shape:

- `from:` keeps the result scoped to known builders.
- `OR` gives one recent-search request instead of one timeline request per account.
- `-is:retweet -is:reply` reduces low-signal social noise.
- The query is well under the X recent-search 512-character query limit.

## Local dry-run configuration

Do not paste the real token into chat, docs, code, `.env`, or committed files. Use a local shell variable only.

```bash
export X_API_ENABLED=1
export X_BEARER_TOKEN='[REDACTED]'
export X_API_FORCE_RUN=1
export X_API_QUERY='(from:karpathy OR from:swyx OR from:amasad OR from:sama OR from:garrytan) -is:retweet -is:reply'
export X_API_MAX_RESULTS=10
export X_API_DAILY_POST_LIMIT=10

python scripts/update_news.py \
  --output-dir /tmp/ai-news-radar-x-demo \
  --window-hours 24 \
  --rss-max-feeds 0
```

Then inspect only status and counts:

```bash
python - <<'PY'
import json
p=json.load(open('/tmp/ai-news-radar-x-demo/source-status.json'))
print('x_api=', p.get('x_api'))
print('sites=', p.get('successful_sites'), '/', p.get('site_count'))
PY
```

Expected signs of success:

- `x_api.enabled == true`
- `x_api.ok == true`
- `x_api.item_count` is between `0` and `10`
- `x_api.estimated_cost_usd <= 0.05`

`item_count == 0` can still be technically successful if none of the demo accounts have matching posts in the recent search window.

## Scheduled GitHub Actions configuration

For a forked/private maintainer repo, put the token in GitHub Secrets, not in files:

- Secret: `X_BEARER_TOKEN`

Use GitHub Actions Variables or repository env values for the non-secret knobs:

```text
X_API_ENABLED=1
X_API_QUERY=(from:karpathy OR from:swyx OR from:amasad OR from:sama OR from:garrytan) -is:retweet -is:reply
X_API_MAX_RESULTS=10
X_API_DAILY_POST_LIMIT=10
X_API_RUN_UTC_HOUR=0
X_API_RUN_UTC_MINUTE_MAX=10
```

The workflow may run every 30 minutes, but the X API adapter only runs inside the configured UTC window unless `X_API_FORCE_RUN=1` is set. Do not set `X_API_FORCE_RUN=1` in a recurring workflow.

## Cost guardrail

Using the current conservative pricing assumption from `docs/research/advanced-source-free-tier-budget-2026-05-10.md`:

```text
10 returned posts × $0.005 per post read = up to $0.05 per demo run
```

This is safer than cloning Follow Builders' full strategy:

```text
25 accounts × 5 returned posts/account = up to 125 post reads/day ≈ $0.625/day
```

## When to scale up

Only increase after the demo is verified:

1. 5 accounts / 10 posts / once daily — demo safe default.
2. 10 accounts / 20 posts / once daily — small private tracker.
3. 25 accounts — only if the maintainer explicitly accepts X API spend and maintenance.

For most public AI News Radar users, prefer the existing Follow Builders public feed instead of direct X API.
