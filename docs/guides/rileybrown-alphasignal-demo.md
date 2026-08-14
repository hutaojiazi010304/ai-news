# Riley Brown + AlphaSignal Advanced Source Demo

Date: 2026-05-11
Scope: a one-account X API demo and one-newsletter AgentMail demo for AI News Radar / 伯乐Skill onboarding.

## Goal

Show that AI News Radar can connect private advanced sources without making them public defaults:

1. X API: read public posts from a single builder account, `@rileybrown`.
2. AgentMail: read metadata-only newsletter messages from a single AlphaSignal subscription.

No real token, inbox id, mailbox address, or email body should be written to the repo.

## X API: single-account demo

Use recent search with one `from:` account instead of a timeline crawler:

```bash
export X_API_ENABLED=1
export X_BEARER_TOKEN='[REDACTED]'
export X_API_FORCE_RUN=1
export X_API_QUERY='from:rileybrown -is:retweet -is:reply'
export X_API_MAX_RESULTS=10
export X_API_DAILY_POST_LIMIT=10

.venv/bin/python scripts/update_news.py \
  --output-dir /tmp/ai-news-radar-rileybrown-x \
  --window-hours 24 \
  --rss-max-feeds 0
```

Check status only:

```bash
.venv/bin/python - <<'PY'
import json
p=json.load(open('/tmp/ai-news-radar-rileybrown-x/source-status.json'))
print('x_api=', p.get('x_api'))
PY
```

Expected:

- `x_api.enabled == true`
- `x_api.ok == true` when the token is valid and the request succeeds
- `x_api.item_count <= 10`
- `x_api.estimated_cost_usd <= 0.05`

`item_count == 0` can still be a valid API test if the account has no matching public post inside the recent-search window.

Do not set `X_API_FORCE_RUN=1` in recurring GitHub Actions.

## AgentMail: single AlphaSignal newsletter demo

AlphaSignal homepage: `https://alphasignal.ai/`

Observed signup surface:

- The homepage has an email input with placeholder `Your best email...` and a `SUBSCRIBE` button.
- No public RSS/Atom feed was found in the homepage HTML during this check.
- The site uses a newsletter signup flow, so AgentMail is an appropriate private-source route.

Recommended setup:

1. Create or choose an AgentMail inbox dedicated to newsletter demos.
2. Subscribe that inbox address at `https://alphasignal.ai/`.
3. Keep publishing disabled until the digest is inspected.
4. Filter output to AlphaSignal sender domains so a shared inbox does not leak unrelated newsletter metadata.

Local run:

```bash
export EMAIL_DIGEST_ENABLED=1
export EMAIL_DIGEST_PUBLISH=0
export AGENTMAIL_API_KEY='[REDACTED]'
export AGENTMAIL_INBOX_ID='[REDACTED]'
export AGENTMAIL_LIMIT=10
export AGENTMAIL_ALLOWED_SENDER_DOMAINS=alphasignal.ai

.venv/bin/python scripts/update_news.py \
  --output-dir /tmp/ai-news-radar-alphasignal-mail \
  --window-hours 168 \
  --rss-max-feeds 0
```

Check status only:

```bash
.venv/bin/python - <<'PY'
import json
p=json.load(open('/tmp/ai-news-radar-alphasignal-mail/source-status.json'))
print('agentmail=', p.get('agentmail'))
PY
```

Expected:

- `agentmail.enabled == true`
- `agentmail.ok == true` when credentials are valid
- `agentmail.allowed_sender_domains == ['alphasignal.ai']`
- `agentmail.item_count` is the number of matching metadata-only messages

If `item_count == 0`, check whether AlphaSignal has sent a confirmation email or whether the actual sender domain is a subdomain like `mail.alphasignal.ai`. The filter already accepts subdomains of `alphasignal.ai`.

## GitHub Actions variables for scheduled private demo

Secrets:

```text
X_BEARER_TOKEN
AGENTMAIL_API_KEY
AGENTMAIL_INBOX_ID
```

Variables:

```text
X_API_ENABLED=1
X_API_QUERY=from:rileybrown -is:retweet -is:reply
X_API_MAX_RESULTS=10
X_API_DAILY_POST_LIMIT=10
X_API_RUN_UTC_HOUR=0
X_API_RUN_UTC_MINUTE_MAX=10

EMAIL_DIGEST_ENABLED=1
EMAIL_DIGEST_PUBLISH=0
AGENTMAIL_LIMIT=10
AGENTMAIL_ALLOWED_SENDER_DOMAINS=alphasignal.ai
```

Keep `EMAIL_DIGEST_PUBLISH=0` for public repos unless the maintainer explicitly wants to publish a redacted metadata digest.
