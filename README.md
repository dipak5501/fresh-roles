# Fresh Roles

A local job desk for **new public postings**. It matches jobs to a profile you edit, drafts a short application note, and keeps a queue of what you still need to do.

You send every application yourself on the employer site.

## Run it

From this folder:

```bash
python3 app.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Python 3.9 or newer. Plain text and DOCX need nothing extra. To read PDF text locally, install one library:

```bash
python3 -m pip install -r requirements.txt
```

`pypdf` runs on this computer. Resume files are not sent anywhere.

The site stays on this computer (`127.0.0.1` only). Stop it with Ctrl+C.

## What you do in the app

1. Edit the example profile, or use **Upload resume** (PDF, DOCX, `.txt`, or `.md`). Parsing stays on this computer. The original file is not saved. Review the fields, then click **Save profile**. Every extracted field stays editable.
2. Click **Refresh new jobs**. The first pull can take half a minute.
3. Open a job. Read the match reasons. Click **Draft application note**, edit it, and copy it.
4. Click **Open the application page** and submit on that site yourself.
5. Mark the job **Ready** when the note looks right, **Applied** only after you have submitted it, or **Skipped**.

Statuses: **new**, **drafted**, **ready**, **applied**, **skipped**.

## What this app does not do

- It does not sign in to JobRight, LinkedIn, Indeed, Greenhouse, or any other site.
- It does not store a password, cookie, or session.
- It does not bypass CAPTCHAs.
- It does not submit, schedule, or send applications for you.
- It does not keep running a background loop after you close it.

There is no password field. Do not put a password in the profile or the draft.

## Refresh new jobs

Use the **Refresh new jobs** button, or run:

```bash
python3 app.py refresh
```

That command checks the public feeds once and exits. Jobs already seen stay in `data/store.json`, so the next run highlights posts that were not stored before.

To repeat the check every 10 minutes, add this cron line yourself if you want it. It only updates the local list. It does not submit applications.

```bash
*/10 * * * * cd "/Users/dslc/Try Job hunt" && /usr/bin/python3 app.py refresh >> "/Users/dslc/Try Job hunt/data/refresh.log" 2>&1
```

Source timing, so the 10-minute command does not hammer free APIs:

| Source | How often this app calls it |
| --- | --- |
| Remote OK | At most every 10 minutes |
| Jobicy | At most every 10 minutes |
| Arbeitnow | At most about once an hour (their feed updates hourly) |
| Remotive | At most every 6 hours (their terms allow only a few calls a day) |

If a source was checked recently, refresh skips it and says so.

## Where files live

- `data/profile.example.json` — placeholder profile you can copy
- `data/profile.json` — your name, contact, targets, skills, summary, and filters. This file stays on this computer and is not part of the git repo.
- `data/store.json` — jobs already seen, drafts, and statuses (created on first refresh, also not committed)

If the profile file is missing, the app creates it on startup. A scanned PDF with no text layer is reported in the page and does not invent a name or skills.

To pull the feeds again from scratch, quit the app and delete `data/store.json`. Your profile file stays.

## Job sources

Listings are read from public JSON feeds and linked back to the original posting:

- [Remotive](https://remotive.com)
- [Arbeitnow](https://www.arbeitnow.com)
- [Remote OK](https://remoteok.com)
- [Jobicy](https://jobicy.com)

Filters (keyword, role, location, status, minimum match) are saved in the profile file. Matching uses your target titles, skills, keywords, and location. The list is ordered with the newest posts first. Minimum match starts at 1, so posts with no overlap stay hidden until you set it to 0.

## If it will not start

- `Address already in use`: quit the other copy, or run `FRESH_ROLES_PORT=8766 python3 app.py`.
- A source failed: the others still load. Use **Refresh new jobs** again later.
- Empty list: clear the keyword, role, and location filters, set minimum match to 0, and open the **All** tab.
