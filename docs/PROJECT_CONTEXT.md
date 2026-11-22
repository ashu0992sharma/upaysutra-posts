# UpaySutra social posts: project context

The shared brief for anyone working on this project: Claude Code, Claude Cowork, a
claude.ai Project, or a person. It records **what we decided and why**. The code is
the source of truth for *how*.

**Keeping it current:** when a discussion settles something, add a dated line to the
[Decision log](#decision-log) and update the section it changes. Discussing in another
tool? Ask it to write the decision as one log line (date · what · why) and add it
here (Cowork can edit this file directly), or paste it into Claude Code.

Last updated: 2026-11-22

---

## What this is

`postgen` builds and publishes daily Instagram and Facebook posts for **UpaySutra**,
a Hindu astrology app (Play Store id `com.astro.upaysutra`). The goal is page growth
that leads to app installs: followers should find each post useful enough to follow
the page.

Owner: Ashutosh Sharma. Repo: `ashu0992sharma/upaysutra-posts` (private).

## How it runs (fully automated)

| When (IST) | What |
|---|---|
| Saturday 4:00 PM | GitHub Actions builds next Monday–Sunday: captions, images, reels, schedule. Summary email. |
| Daily 6:00 AM | Morning post (a **reel**; the carousel is the fallback if no reel was built) |
| Daily 6:15 AM | Morning story |
| Daily 8:00 PM | "Kal ka din" post (preview of tomorrow) |
| Daily 8:15 PM | Evening story |
| Daily 8:30 PM | Preview email of tomorrow's posts |

- **Veto mode:** posts go out unless paused. To stop one, run the **pause** workflow
  with a post id (`2026-11-12_post_am`), a date, or `all`. Plan: switch to `auto` mode
  (no preview emails) after the trial.
- A Cloudflare cron starts the publisher at 6:00, 6:15, 6:30 AM and 8:00, 8:15, 8:30 PM IST
  (the :30 runs retry failures); GitHub's own schedule adds a 7:00 AM / 9:00 PM fallback.
  It never posts more than 2 hours late, and skips a slot if something is already posted
  around that time.
- **Catch-up:** to send a post that's over 2 hours late, run the publish workflow with its
  post id in the "item" box (e.g. `2026-11-22_story_pm`).
- The weekly build skips anything already built (captions, images, reels). Use the
  workflow's "Regenerate" box or `--force` to rebuild.
- Stack: GitHub Actions; Cloudflare R2 (private state bucket + public media bucket);
  Meta Graph API; Gmail SMTP for alerts. Setup steps: [AUTOMATION_SETUP.md](AUTOMATION_SETUP.md).

## Hard rules (accuracy)

- **Dates, tithis and timings never come from the AI.**
  - Festival and vrat dates: hand-curated from drikpanchang.com (New Delhi) in
    `config/calendar/<year>.yaml`.
  - Daily timings (tithi, nakshatra, yoga, sunrise/sunset, Rahu Kaal, Abhijit): computed
    offline by the `kalagana` library, which matches Drik to within ~3 minutes.
  - kalagana's own festival rules were rejected: 23 of ~130 checks against Drik 2026 were wrong.
- Festival muhurat and moonrise times are curated from Drik in the calendar file. If a
  major festival has none, the build email flags it and the post goes out without them.
- The AI only *phrases* things: hooks, meaning, upay wording, rashifal sentences
  (from computed transits). Guardrails reject invented facts, fear, certain
  predictions, health or money promises.
- Times show to the minute and should match Drik. Never write "कल/kal" for
  after-midnight times; use "देर रात" / "अगली सुबह".
- Check every generated background at full size: image models get iconography wrong
  (duplicate rashis on zodiac wheels, wrong chakras). An automatic Claude vision check
  rejects blockers.

## Content

**Morning (6 AM)**
- Festival days: festival post with its upay and timings.
- Ordinary days: "Aaj ka Panchang" plus **3 rashis' rashifal**.
- Posted as a reel (see below). The carousel version is the fallback: 1 panchang, 2–4
  rashis, 5 app slide.

**Evening (8 PM):** "Kal ka din", a preview of tomorrow. Festival eves use the
festival's look; otherwise tomorrow's weekday deity.

**Stories:** key timings plus an app link. IG link stickers can't be added by the API,
so stories say "link in bio".

**Captions:** what people act on comes first, before "See more": hook → upay → timings
→ significance. The app is mentioned in 1 of 3 posts; Panchang days use the rashifal
CTA instead.

**Rashifal**
- 3 rashis per Panchang day. Readings come from kalagana gochara (transits from the
  Moon sign); the AI turns them into plain guidance.
- At most one graha is named, with no lucky colours or numbers.
- Rotation: all 12 rashis every 4 Panchang days. Each cycle is shuffled, so the groups
  change and no rashi appears two days running.
- CTA: other rashis' rashifal and astrologers are on the app. The app shows each user
  their own rashi, so the post doesn't list all 12. The user rejected a 12-rashi grid
  and a "baaki 9" count as too much.

**Reels**
- Built from the day's post: one slide per spoken line, with a Hindi voiceover by
  ElevenLabs, voice "Shivank S" (calm male).
- Order: hook → meaning → timing → (Panchang days: 3 rashis) → upay → follow CTA.
- Ordinary reels run 19–28 s. Panchang reels with rashis run ~60 s.
- Words the voice mispronounces go in `settings.yaml` → `reels.pronounce`.

**App links**
- Instagram captions say "(link in bio)". The bio has the get-app and Play Store links.
- Facebook captions carry the Play Store link with a referrer tag.
- Where a typeable link is needed: `upaysutra.com/get-app` (`/app` doesn't exist).
- Facebook stories use the get-app page because Facebook rejects Play Store links there.

## Experiments running

- **Reels test, 5–19 Nov 2026.** Started because reach was ~8–10 people, all followers.
  When it ends, compare reach and the non-follower share against carousels. Also
  compare watch time on ~60 s Panchang reels vs. shorter reels; if people drop off,
  cut the "meaning" line on Panchang days.
- **Posting volume:** 2 feed posts + 2 stories a day. Measure for 3–4 weeks in Insights
  before adding a third feed post (more feed posts can thin reach; stories are the
  cheaper extra touchpoint).

## Open items

- [ ] **GitHub Actions blocked since ~20 Nov 2026** ("recent account payments have failed or
      your spending limit needs to be increased"). Nothing posted 21–22 Nov and the 21 Nov
      weekly build never ran. Fix the payment in GitHub → Billing and plans. Posts for
      22 Nov–6 Dec were built locally and are in R2, waiting.
- [ ] Change the Cloudflare cron to the new publish times (see How it runs).

- [ ] Drik muhurat times for **Bhai Dooj (11 Nov 2026)**. Missing, so the post goes out without them.
- [ ] Facebook 6:15 AM story was skipped on 5 Nov as a false duplicate. The Facebook reel
      the system posted itself probably isn't recognised as its own post.
- [ ] `config/calendar/2027.yaml` must exist before any post after 31 Dec 2026, then run
      `python -m postgen check-calendar --year 2027`. Uncovered dates stop the build on purpose.
- [ ] Switch `automation.mode` from `veto` to `auto` once the trial is trusted.
- [ ] Review the reels test results (~19 Nov).

## Costs and services

- Claude Sonnet: captions, reel scripts, image review.
- Gemini image models: backgrounds, paid per image; there's no free tier. Backgrounds are cached
  and reused on a 4-week rotation.
- ElevenLabs: voice, Starter plan or above for commercial use. Panchang reels use
  about 2× the characters of the others.
- Cloudflare R2: free tier. GitHub Actions: free tier.

## Decision log

- 2026-10-24 · Build `postgen` in milestones and review output before moving on · catch accuracy and visual issues early
- 2026-10-24 · Festival dates from a hand-curated Drik calendar, timings from kalagana · kalagana's festival rules were wrong 23 of ~130 times
- 2026-10-24 · Panchang days carry 3 rotating rashis plus an app CTA · drive installs while staying grounded
- 2026-10-25 · Day folders directly in `output/<date>_<Dow>/`; working files in `output/_data/` · the user couldn't find nested folders
- 2026-10-25 · IG "link in bio", FB caption link, minimal app slide pointing to upaysutra.com/get-app · nothing on an image is clickable
- 2026-10-27 · 2 posts + 2 stories a day (6 AM, 8 PM "Kal ka din") · grow without thinning feed reach
- 2026-10-29 · Fully automated: GitHub Actions + R2 + Meta API, veto mode with 8:30 PM preview email · the user no longer reviews or schedules by hand
- 2026-11-04 · 6 AM slot becomes a voiced reel (Shivank S); test until ~19 Nov · reach was ~10 followers only; reels reach non-followers
- 2026-11-07 · Rashis shuffled each cycle instead of fixed groups of three · the same groups kept repeating
- 2026-11-07 · Panchang reels read the 3 rashis inside the reel · otherwise rashifal vanished from the feed on reel days
- 2026-11-07 · Weekly build skips anything already built; `--force` rebuilds · a second build shouldn't spend API credits again
- 2026-11-07 · This file is the shared context across Claude Code, Cowork and claude.ai · discussions happen in several tools
- 2026-11-22 · Publish workflow takes an optional post id that skips the 2 h late limit · manual catch-up after the Actions billing block
- 2026-11-22 · Publisher runs only at slot times (6 runs + 2 fallbacks a day) instead of every 5 min · ~50 mostly idle runs a day used ~1,000 Actions minutes by 20 Nov
