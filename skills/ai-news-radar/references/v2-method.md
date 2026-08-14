# V2 Method

Use this reference when AI News Radar work is about product direction, source
coverage, or packaging the repo as a reusable Skill.

## Method Inputs

- **Office Hours style**: understand demand, status quo, narrowest wedge, and
  alternatives before committing to a product direction.
- **Superpowers style**: turn the chosen direction into a small spec, exact file
  plan, tests, review, and verification before calling the work complete.

## Product Diagnostic

Run this before expanding features:

1. **User**: ordinary AI enthusiast, maintainer, or agent using the Skill?
2. **Current workaround**: noisy timelines, manual site checks, RSS reader, email,
   or another aggregator?
3. **Demand evidence**: is the user asking for breadth, speed, trust, or fewer
   choices?
4. **Narrowest useful default**: what can ship without asking a new user to
   configure keys, inboxes, cookies, or many toggles?
5. **Advanced escape hatch**: what belongs in OPML, GitHub Secrets, or a local
   adapter instead of the public default?
6. **Success signal**: what should improve in `source-status.json`, the Signal
   view, or the maintainer workflow?

Ask the user only when a missing answer changes the implementation choice. For
mobile users, prefer one short multiple-choice question.

## Approach Selection

For unclear product choices, compare 2-3 approaches:

- **Minimal viable**: smallest diff, safest public default, fastest validation.
- **Durable architecture**: cleaner long-term extension point, better tests or
  docs, more future-proof source coverage.
- **Packaged variant**: makes the project easier for Codex/Claude users to fork,
  customize, and deploy as their own AI news radar.

Recommend one approach and state what evidence would make the recommendation
wrong.

## Source Coverage Ladder

Use the highest stable rung available:

1. Official RSS/Atom/JSON from the source owner.
2. Public GitHub-generated feed files produced by a maintained repo.
3. Public newsletter archive or archive RSS.
4. Public static page with stable timestamps and selectors.
5. OPML for personal or niche feeds.
6. Optional API adapter with user-owned secrets.
7. Private inbox, cookies, browser sessions, or unstable bridges.

Rungs 1-4 can become public defaults if they are useful to most visitors. Rungs
5-7 belong in advanced documentation unless the user explicitly wants a private
fork.

## GitHub Feed Intake

When given a GitHub repo that collects news:

1. Read `README*`, `.github/workflows/*`, `config/*`, and generated feed files.
2. Identify whether the repo already uses official APIs or cron jobs centrally.
3. Prefer consuming its public raw output instead of copying its crawler.
4. Verify schema fields: title/text, canonical URL, source/person, timestamp,
   stable ID, and generation cadence.
5. Add tests around parser behavior before relying on it as a built-in source.

This is the preferred pattern for X-heavy projects such as curated builder feeds:
the public repo owns API credentials, this project consumes only public JSON.

## Engineering Loop

Keep implementation disciplined:

1. Map files before editing.
2. For behavior changes, add focused parser/filter tests.
3. Implement the smallest code path that passes the test.
4. Run `python -m py_compile scripts/update_news.py` and `pytest -q`.
5. For Skill changes, run the Skill validator.
6. For source changes, run a local generation into `/tmp` and inspect
   `source-status.json`.
7. After push, watch GitHub Actions and Pages deployment when relevant.

Do not claim a source is covered until a fetcher, OPML path, public generated
feed path, or optional-secret path is documented and validated.

## V2 Definition Of Done

A v2 change is complete when:

- The default page remains simple for ordinary users.
- The first viewport shows source health and coverage as status signals, not a
  pile of configuration controls.
- Source coverage is documented as public default vs advanced/private.
- New built-in sources have stable timestamps and do not require secrets.
- Failures are visible in `source-status.json` instead of silently disappearing.
- The Skill tells future agents how to add, reject, or route similar sources.
