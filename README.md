# Lantern

Paste this into the browser address bar: `http://127.0.0.1:8765`

If that page does not load, start it from this folder:

```bash
python3 app.py
```

Then paste `http://127.0.0.1:8765` into the address bar again. It stays on this computer. It is not a public website, and there is no GitHub website link for it.

A local job desk for **new public postings**. It scores each job against your profile, explains the fit and the gaps, drafts a job-specific resume emphasis and cover note, and keeps a supervised queue.

You send every application yourself. Clicking a job opens the original posting in a new tab. This app does not submit it.

The list starts as United States only, and on posts from the last hour. The time comes from the feed. A job with no posting date is left out of Last hour. The Posted control can switch to 24 hours, 7 days, or any time. A remote job stays when it is US-remote or does not name another country. Jobs with no location, and jobs in places such as the UK, EU, Canada, India, or Australia, stay hidden until you turn that filter off.

## Run it

From this folder:

```bash
python3 app.py
```

Then paste `http://127.0.0.1:8765` into the address bar.

Python 3.9 or newer. Plain text and DOCX need nothing extra. To read PDF text locally, install one library:

```bash
python3 -m pip install -r requirements.txt
```

`pypdf` runs on this computer. Resume files are not sent anywhere.

The site stays on this computer (`127.0.0.1` only). Stop it with Ctrl+C.

## What you do in the app

1. Edit the example profile, or use **Upload resume** (PDF, DOCX, `.txt`, or `.md`). Parsing stays on this computer. The original file is not saved. Review the fields, then click **Save profile**. Every extracted field stays editable.
2. Click **Refresh new jobs**. The first pull can take half a minute.
3. Click a job title. The original posting opens in a new tab, and the match stays on this page: score, why it fits, gaps, and a tailored draft.
4. Use **Review match** if you only want the desk panel. Use **Add to supervised queue** when you want a prepared draft. That run stops at **Ready**. It does not submit anything.
5. Open the original posting and submit it yourself. Then click **I applied**. That job stays in the **Applied** list on this computer, with the title, company, location, original link, match score, and the time you marked it.
6. On that list you can add a note, set a follow-up date, and later change the status to **Interviewing**, **Offer received**, **Rejected**, or **Archived**. The applied time stays. Search and sort the list, and use **Download CSV** to save a copy on this computer.
7. Use **Save** to bookmark a job without marking it applied. Sort matches by newest or by match score. The keyword box searches the current list.

Statuses: **new**, **drafted**, **ready**, **applied**, **interviewing**, **offer**, **rejected**, **archived**. Older **skipped** jobs are kept as **archived**.

The weekly target is a reminder. It does not block the queue. The Applied list shows how many you marked this week and how many are in each status.

Gaps are skills the posting emphasizes that are not on your profile. A skill that is only on your resume is not a gap. The matches view also lists which missing skills show up most often in the current United States postings. Drafts use your profile text. They do not invent employers, dates, degrees, or skills.

## Local model

If [Ollama](https://ollama.com) is running on this Mac, Lantern uses an installed model for the match notes and the tailored summary. Resume text is not sent to a remote model. The page says whether a note came from that local model or from the built-in matcher.

On this computer the model is `qwen2.5vl:3b` through Ollama, which loads it on the Apple GPU. If the badge says the built-in matcher, open the Ollama app so it is listening on `127.0.0.1:11434`. You do not need to download another model for the desk to work.

## What this app does not do

- It does not sign in to JobRight, LinkedIn, Indeed, Greenhouse, or any other site.
- It does not store a password, cookie, or session.
- It does not bypass CAPTCHAs.
- It does not submit, schedule, or send applications for you. Supervised mode only prepares jobs you add, then stops so you can review them.
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
