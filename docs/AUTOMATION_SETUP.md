# Automation setup (one time, ~1 hour)

After this, posts build themselves every Saturday and publish at 6:00/6:15 AM and
8:00/8:15 PM IST. You get a preview email every night at 8:30 PM (veto mode) and an
email whenever something needs attention.

You'll collect **14 secrets** along the way; the table at the end lists them all.

---

## 1. GitHub (private repo)
1. Create a **private** repository, e.g. `upaysutra-posts` (github.com → New).
2. Tell Claude the repo URL; Claude pushes the code (your `.env`, images and outputs
   are never committed: they live in the private bucket).

## 2. Cloudflare R2 (image hosting + saved state)
Free tier covers this (10 GB storage, no egress fees).
1. dash.cloudflare.com → sign up / log in → **R2 Object Storage** → enable.
2. **Create bucket** `upaysutra-state` → keep it **private**.
3. **Create bucket** `upaysutra-media` → Settings → **Public access** → enable the
   `r2.dev` subdomain (or connect a custom domain like `media.upaysutra.com`).
   Copy the public URL, e.g. `https://pub-xxxx.r2.dev`.
4. R2 → **Manage API tokens** → Create token → permission **Object Read & Write**,
   scoped to both buckets → copy **Access Key ID**, **Secret Access Key** and your
   **Account ID** (shown on the R2 overview page).

Secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET` = `upaysutra-state`, `R2_MEDIA_BUCKET` = `upaysutra-media`,
`MEDIA_BASE_URL` = the public URL from step 3.

## 3. Meta (Instagram + Facebook publishing)
Prerequisite: the Instagram account is a **Business/Creator** account linked to the
UpaySutra Facebook Page (Instagram → Settings → Account type; Page → Linked accounts).
1. **developers.facebook.com** → My Apps → **Create app** → type **Business** →
   name `UpaySutra Poster` → link it to your business portfolio.
2. In the app, add the products **Instagram** (API setup with Facebook login) and
   **Facebook Login for Business**.
3. **business.facebook.com** → Settings → **Users → System users** → Add →
   name `upaysutra-poster`, role **Admin**.
4. On that system user: **Assign assets** → your **Page** (full control) and your
   **Instagram account** (full control).
5. **Generate new token** → choose the app from step 1 → expiry **Never** → tick:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`, `business_management` → copy it.
6. IDs: open **developers.facebook.com/tools/explorer**, paste the token, and run
   `me/accounts` → copy your Page's `id`; then run
   `<PAGE_ID>?fields=instagram_business_account` → copy that `id`.

Secrets: `META_ACCESS_TOKEN`, `META_PAGE_ID`, `META_IG_USER_ID`.
(If a first publish fails with a permissions error, send the error to Claude. Apps
that only post to your own assets normally work without Meta App Review.)

## 4. Email alerts (Gmail)
1. myaccount.google.com → Security → turn on **2-Step Verification** (if off).
2. Security → **App passwords** → create one named `UpaySutra posts` → copy the
   16-character password.

Secrets: `SMTP_USER` = your Gmail address, `SMTP_PASSWORD` = the app password,
`ALERT_EMAIL_TO` = where alerts go (can be the same address).

## 5. API keys you already have
Secrets: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (from your `.env`).

## 6. Add everything to GitHub
Repo → **Settings → Secrets and variables → Actions**:
- **Secrets** → New repository secret → add all 14 below.
- **Variables** → New repository variable → `DRY_RUN` = `true` (trial mode).

| Secret | Where from |
|---|---|
| ANTHROPIC_API_KEY | `.env` |
| GEMINI_API_KEY | `.env` |
| R2_ACCOUNT_ID | step 2.4 |
| R2_ACCESS_KEY_ID | step 2.4 |
| R2_SECRET_ACCESS_KEY | step 2.4 |
| R2_BUCKET | `upaysutra-state` |
| R2_MEDIA_BUCKET | `upaysutra-media` |
| MEDIA_BASE_URL | step 2.3 |
| META_ACCESS_TOKEN | step 3.5 |
| META_PAGE_ID | step 3.6 |
| META_IG_USER_ID | step 3.6 |
| SMTP_USER | step 4 |
| SMTP_PASSWORD | step 4 |
| ALERT_EMAIL_TO | step 4 |

## 7. Go live (Claude does this with you)
1. Upload the current image library and state to R2 once (`python -m postgen sync up`).
2. **Trial**: run *Build next week* manually → summary email; publishing runs in
   `DRY_RUN` mode (logs what it *would* post) and preview emails arrive nightly.
3. **First live post**: set `DRY_RUN` = `false` before a 6 AM slot; check Instagram
   and Facebook right after.
4. Veto mode for 2 weeks: read the 8:30 PM email; to stop a post, GitHub app →
   repo → Actions → **Pause a post** → Run workflow → paste the post id or date.
5. Then switch `automation.mode` to `auto` in `config/settings.yaml`.

## Still manual (~15 min/month)
- Festival dates and Drik timings: add the next month to `config/calendar/<year>.yaml`.
  The build emails you 3 weeks before the calendar runs out, and warns when a major
  festival has no curated timings. Claude can do this with you.
- Nothing else: a skipped or failed post is emailed; nothing is ever posted with
  missing or guessed facts.
