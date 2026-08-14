# AI News Radar V2 Product Brief

Generated from an Office Hours style product diagnostic plus a Superpowers style
implementation loop.

## Problem Statement

Ordinary AI enthusiasts want a high-signal AI update page they can open without
building a personal RSS/X/email pipeline. Maintainers and agent users need a
clear path to add their own sources without breaking the public default.

## Demand Evidence

- The user asked for broad distribution, not just a personal dashboard.
- Too many source choices were called out as a problem for newcomers.
- X and email were discussed as valuable but unreliable as defaults.
- GitHub-generated public feeds such as Follow Builders were preferred because
  they centralize unstable API work and expose stable public outputs.

## Status Quo

People currently mix manual site visits, noisy X timelines, newsletter inboxes,
RSS readers, and aggregator sites. Each path covers part of the signal, but none
is both low-noise and easy to fork.

## Narrowest Useful Wedge

Keep the default page as a simple 24h signal board, but make the source model
visible: source health, AI signal density, official/newsletter coverage,
builders/X coverage, aggregator breadth, and private extension readiness.

## Approaches Considered

### A: Add More Sources

Fast to make the feed look richer, but risks noise and does not explain the
coverage model.

### B: Surface Coverage As Product Signal

Adds first-viewport trust without adding controls. It helps users understand why
the project is more than another aggregator.

### C: Build Full Self-Hosted X/Email Adapters

Powerful for private forks, but too credential-heavy and fragile for the public
default.

## Recommended Approach

Use Approach B now. Keep Approach C as advanced/private integration guidance.

## Success Criteria

- First viewport answers: what is covered, whether sources are healthy, and how
  private extensions fit.
- No extra default choices are required from ordinary readers.
- X/email remain optional and secret-backed, not public defaults.
- Future agents can route a new source into public default vs advanced/private.
