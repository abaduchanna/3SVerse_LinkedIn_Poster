# 3SVerse LinkedIn Campaign Uploader

Version 1.2.0 schedules exactly one LinkedIn company-page post per campaign day. It commits the schedule date/time first, returns to the scheduled composer, and only then inserts and verifies the complete caption and hashtags before the final Schedule click. It now keeps persistent per-day status, labels earlier campaign days Skipped when starting from the middle, and continues trusted automation while its tab is unfocused. The native file chooser remains intercepted, so no Windows Explorer popup is left open.

Separate Chrome extension for the **3S Verse LinkedIn company page**. It does not modify the Meta Planner Scheduler.

## What it does

- Links one campaign folder, or two when captions and media are split.
- Reads complete `## Day N` captions and keeps their hashtags on separate lines.
- Matches `day-10.png` and `3SVerse-Reel-10.mp4` to Day 10.
- Selects either the daily campaign images or the daily campaign videos, producing one post per Day entry—not duplicate image and video posts.
- Uses the inspected LinkedIn company-page route and exact controls: **Create → Start a post → Add media → Next → Schedule post → Date/Time → Next → Text + hashtags → verify → final Schedule**.
- Day 1 uses the selected start date/time, Day 2 uses the next date at the same time, and so on.
- Waits for LinkedIn to confirm each scheduled post before preparing the next campaign day.

## Install

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Choose **Load unpacked**.
4. Select this `linkedin-campaign-uploader` folder.
5. Pin the blue LinkedIn `in` icon.

## Use

1. Click the LinkedIn extension icon.
2. Link the folder containing the campaign Markdown and images. Link the second folder only when videos are separate.
3. Select daily campaign Images or Videos.
4. Select the starting campaign day, start date, and daily time.
5. Click **Schedule all daily posts**.

The linked folder is the normal upload source. Windows paths are optional fallbacks only.

The extension uses LinkedIn's live-inspected `Schedule post` fields (`Date`, `Time`, `Next`) and final `Schedule` action.

---

## Provenance (LinkedIn Poster repo)

This is the **full, unmodified source** of the LinkedIn Campaign Uploader
extension (v1.2.0) from
`abaduchanna/3SVerse_Social_Campaign_Assistant` → `linkedin-campaign-uploader/`.

It is shipped here as the **workflow reference for Browser Mode** in the
LinkedIn Poster: `web_workflow.py` is a command-for-command Python/Selenium
port of this extension (same CDP trusted input, same locators, same
schedule-first-then-caption order). The extension itself remains installable
from this folder (Load unpacked) exactly as documented above.
