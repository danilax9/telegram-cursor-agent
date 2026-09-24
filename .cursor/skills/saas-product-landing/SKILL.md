---
name: saas-product-landing
description: Builds modern SaaS marketing pages for telegram-cursor-agent — light surfaces, product mock UI, repo CTA, no retro layouts. Use when the user asks for a landing, promo site, cursor.coreless.site, or selling page for this bot.
---

# SaaS landing (telegram-cursor-agent)

## Non-negotiable look

- **Modern SaaS 2024–2026**, not blog/2010: sticky nav, centered hero, product screenshot/mock, bento features, FAQ accordion, dark or light **product** palette — not newspaper typography stacks.
- **Light mode default** unless user asks dark: off-white background, near-black text, one sharp accent (lime/teal/coral — pick one, not purple gradient cliché).
- **Typography**: geometric sans (Geist, Söhne-like, DM Sans, Plus Jakarta) — never “editorial serif hero + italic quotes only”.
- **Product proof**: include a **UI mock** (chat window, dashboard frame, browser chrome) — not only text columns.
- **Spacing**: 8px grid, generous section padding (64–96px desktop), clear hierarchy.

## Structure (marketing)

1. Hero — outcome headline, one line sub, **primary CTA → GitHub repo** (not private bot link unless user says so).
2. Product shot — mock Telegram/agent thread or server diagram.
3. Benefits — bento grid, **varied** cell sizes (not 3 identical cards).
4. FAQ — real objections (ChatGPT? need laptop? access?).
5. Final CTA — repeat repo link.

## Copy (Russian default for this product)

- Benefit-led headlines, no feature soup.
- Sell **phone → server → same chat**, not “AI magic”.
- No fake testimonials or logo rows.

## Deploy for this repo

- Source: `landing/index.html` (+ assets alongside).
- Publish: `/var/www/cursor.coreless.site/` + nginx already configured.
- After edit: `cp landing/*` to web root and verify `curl -I https://cursor.coreless.site`.

## Before saying “done”

- [ ] Looks like a **product site**, not a manifesto page
- [ ] Mobile nav usable, touch targets ≥44px
- [ ] One H1, meta description, favicon
- [ ] CTA points to `https://github.com/danilax9/telegram-cursor-agent`
- [ ] No Inter-only purple-gradient template vibes
