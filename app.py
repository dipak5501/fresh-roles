#!/usr/bin/env python3
"""Fresh Roles: search new public job posts and draft notes locally.

This program does not sign in anywhere and does not submit applications.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from resume_extract import resume_from_upload

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
PROFILE_PATH = DATA / "profile.json"
STORE_PATH = DATA / "store.json"
LOCK_PATH = DATA / ".lock"

STATUSES = ("new", "drafted", "ready", "applied", "skipped")
USER_AGENT = "FreshRoles/1.0 (personal local job search; links back to the source listing)"

SOURCES = {
    "remotive": {
        "label": "Remotive",
        "min_interval": 6 * 3600,
        "policy": "Remotive allows only a few API calls per day",
        "home": "https://remotive.com",
    },
    "arbeitnow": {
        "label": "Arbeitnow",
        "min_interval": 60 * 60,
        "policy": "Arbeitnow asks callers not to abuse the free API",
        "home": "https://www.arbeitnow.com",
    },
    "remoteok": {
        "label": "Remote OK",
        "min_interval": 10 * 60,
        "policy": "Remote OK is checked at most every 10 minutes",
        "home": "https://remoteok.com",
    },
    "jobicy": {
        "label": "Jobicy",
        "min_interval": 10 * 60,
        "policy": "Jobicy is checked at most every 10 minutes",
        "home": "https://jobicy.com",
    },
}

DEFAULT_PROFILE = {
    "name": "Your Name",
    "contact": "",
    "target_titles": ["Software Engineer", "Backend Engineer"],
    "skills": ["Python", "JavaScript", "SQL", "APIs"],
    "location": "Remote",
    "keywords": ["python", "backend"],
    "summary": (
        "Replace this with a short summary of your experience and what you want next. "
        "This text is copied into every cover note, so write it in your own words."
    ),
    "filters": {
        "keyword": "",
        "role": "",
        "location": "",
        "status": "new",
        "min_score": 1,
    },
}

LOCATION_ALIASES = {
    "united states": ["united states", "usa", "u.s.", "u.s.a."],
    "usa": ["usa", "united states", "u.s.", "u.s.a."],
    "us": ["usa", "united states", "u.s."],
    "united kingdom": ["united kingdom", "uk", "u.k."],
    "uk": ["uk", "united kingdom", "u.k."],
}

REMOTE_WORDS = ("remote", "worldwide", "anywhere", "global", "work from home", "wfh")
MAX_JOBS = 1200

_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?>.*?</\1>")
_BREAK = re.compile(r"(?i)<br\s*/?>")
_BLOCK_END = re.compile(r"(?i)</(p|div|li|h[1-6]|tr|section)>")
_LIST_ITEM = re.compile(r"(?i)<li[^>]*>")
_TAGS = re.compile(r"(?s)<[^>]+>")
_WS = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")
_TERM = re.compile(r"[a-z0-9+#.]{2,}")


class FetchError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextmanager
def locked():
    DATA.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def plain_text(value, limit=5000):
    text = value if isinstance(value, str) else ""
    # Some feeds send HTML, and some send it escaped. Decode, then strip, twice.
    for _ in range(2):
        text = unescape(text).replace("\xa0", " ")
        text = _SCRIPT_STYLE.sub(" ", text)
        text = _BREAK.sub("\n", text)
        text = _LIST_ITEM.sub("\n- ", text)
        text = _BLOCK_END.sub("\n", text)
        text = _TAGS.sub(" ", text)
    text = re.sub(r"<[^>]*$", " ", text)
    text = _WS.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = text.strip()
    if len(text) > limit:
        cut = text[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip()
    return text


def one_line(value, limit=180):
    text = plain_text(value or "", limit=max(limit * 4, limit))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def clip(value, limit):
    if value is None:
        return ""
    return one_line(value, limit)


def as_list(value, limit=30, item_limit=60):
    if isinstance(value, str):
        parts = re.split(r"[,;\n]", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    found = []
    seen = set()
    for part in parts:
        text = one_line(part, item_limit)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            found.append(text)
        if len(found) >= limit:
            break
    return found


def empty_store():
    return {"jobs": {}, "sources": {}, "last_new_ids": [], "last_refresh_at": None}


def load_store():
    if not STORE_PATH.exists():
        return empty_store()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return empty_store()
    data.setdefault("sources", {})
    data.setdefault("last_new_ids", [])
    data.setdefault("last_refresh_at", None)
    return data


def save_store(store):
    write_json(STORE_PATH, store)


def normalize_profile(raw):
    raw = raw if isinstance(raw, dict) else {}
    filters = raw.get("filters") if isinstance(raw.get("filters"), dict) else {}
    status = filters.get("status") or "new"
    if status not in STATUSES and status != "all":
        status = "new"
    try:
        min_score = int(filters.get("min_score") or 0)
    except (TypeError, ValueError):
        min_score = 0
    min_score = max(0, min(100, min_score))
    name = clip(raw.get("name"), 120)
    summary = plain_text(raw.get("summary") or "", limit=2000)
    return {
        "name": name or "Your Name",
        "contact": clip(raw.get("contact"), 160),
        "target_titles": as_list(raw.get("target_titles"), limit=20, item_limit=80),
        "skills": as_list(raw.get("skills"), limit=40, item_limit=40),
        "location": clip(raw.get("location"), 120),
        "keywords": as_list(raw.get("keywords"), limit=40, item_limit=40),
        "summary": summary
        or DEFAULT_PROFILE["summary"],
        "email": clip(raw.get("email"), 120),
        "phone": clip(raw.get("phone"), 40),
        "links": as_list(raw.get("links"), limit=12, item_limit=240),
        "experience": plain_text(raw.get("experience") or "", limit=4000),
        "education": plain_text(raw.get("education") or "", limit=2000),
        "filters": {
            "keyword": clip(filters.get("keyword"), 80),
            "role": clip(filters.get("role"), 80),
            "location": clip(filters.get("location"), 80),
            "status": status,
            "min_score": min_score,
        },
    }


def load_profile():
    if not PROFILE_PATH.exists():
        profile = normalize_profile(DEFAULT_PROFILE)
        write_json(PROFILE_PATH, profile)
        return profile
    try:
        raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = PROFILE_PATH.with_suffix(".json.bak")
        try:
            PROFILE_PATH.replace(backup)
        except OSError:
            pass
        profile = normalize_profile(DEFAULT_PROFILE)
        write_json(PROFILE_PATH, profile)
        return profile
    return normalize_profile(raw)


def save_profile(profile):
    profile = normalize_profile(profile)
    write_json(PROFILE_PATH, profile)
    return profile


def profile_warnings(profile):
    warnings = []
    if profile.get("name", "").strip().lower() in {"", "your name"}:
        warnings.append("The profile name is still “Your Name”. Change it before you send a note.")
    summary = profile.get("summary") or ""
    if summary.startswith("Replace this with"):
        warnings.append("The summary is still the example text. Rewrite it before you send a note.")
    if not profile.get("skills"):
        warnings.append("Add skills so drafts can point at real overlap with each posting.")
    return warnings


def parse_ts(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        if number < 1_000_000_000:
            return 0
        return int(number)
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return parse_ts(float(text))
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def canonical_url(url):
    parts = urlsplit(url.strip())
    query = urlencode(
        [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def clean_tags(tags):
    found = []
    for tag in tags or []:
        if isinstance(tag, dict):
            tag = tag.get("name") or tag.get("tag") or ""
        text = one_line(tag, 40)
        if text and text not in found:
            found.append(text)
        if len(found) >= 12:
            break
    return found


def clean_salary(value):
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        if value <= 0:
            return ""
        return str(int(value))
    text = one_line(value, 80)
    if text.lower() in {"none", "null", "n/a", "na", "0"}:
        return ""
    return text


def money_range(low, high):
    def amount(value):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    start = amount(low)
    end = amount(high)
    if start and end:
        return f"${start:,}–${end:,}"
    if start:
        return f"${start:,}+"
    if end:
        return f"Up to ${end:,}"
    return ""


def contains_term(blob, term):
    term = (term or "").lower().strip()
    if len(term) < 2:
        return False
    pattern = rf"(?<![a-z0-9+#]){re.escape(term)}(?![a-z0-9+#])"
    if re.search(pattern, blob):
        return True
    if term.endswith("s") and len(term) > 3:
        return contains_term(blob, term[:-1])
    return False


def job_is_remote(job):
    if job.get("remote"):
        return True
    location = (job.get("location") or "").lower()
    return any(word in location for word in REMOTE_WORDS)


def location_matches(preference, job_location):
    pref = (preference or "").strip().lower()
    hay = (job_location or "").lower()
    if not pref or not hay:
        return False
    terms = LOCATION_ALIASES.get(pref, [pref])
    for term in terms:
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", hay):
            return True
    return False


def prefers_remote(preference):
    text = (preference or "").strip().lower()
    return text in {"remote", "anywhere", "worldwide", "wfh"} or text.startswith("remote ")


def score_job(job, profile):
    title = (job.get("title") or "").lower()
    blob = " ".join(
        [
            job.get("title") or "",
            job.get("company") or "",
            job.get("location") or "",
            " ".join(job.get("tags") or []),
            job.get("description") or "",
        ]
    ).lower()
    reasons = []
    points = 0
    best = 0
    best_title = ""
    for target in profile.get("target_titles") or []:
        phrase = target.lower().strip()
        if not phrase:
            continue
        if phrase in title:
            best = max(best, 36)
            best_title = target
            continue
        words = [word for word in _TERM.findall(phrase) if len(word) > 3]
        if words and all(contains_term(title, word) for word in words):
            if 28 > best:
                best = 28
                best_title = target
    if best_title:
        points += best
        reasons.append(f"Title aligns with “{best_title}”")

    skill_hits = []
    for skill in profile.get("skills") or []:
        if contains_term(blob, skill):
            skill_hits.append(skill)
    if skill_hits:
        points += min(40, 8 * len(skill_hits))
        shown = ", ".join(skill_hits[:6])
        reasons.append(f"Skills mentioned: {shown}")

    skill_keys = {skill.lower() for skill in skill_hits}
    keyword_hits = []
    for keyword in profile.get("keywords") or []:
        if keyword.lower() in skill_keys:
            continue
        if contains_term(blob, keyword):
            keyword_hits.append(keyword)
    if keyword_hits:
        points += min(15, 5 * len(keyword_hits))
        reasons.append("Keywords: " + ", ".join(keyword_hits[:6]))

    preference = profile.get("location") or ""
    if prefers_remote(preference) and job_is_remote(job):
        points += 10
        reasons.append("Matches your remote preference")
    elif location_matches(preference, job.get("location") or ""):
        points += 12
        reasons.append(f"Location matches “{preference}”")

    if not reasons:
        reasons.append("No strong overlap with your profile yet. Edit titles or skills to tighten this.")
    return {"score": min(100, points), "reasons": reasons[:4], "skill_hits": skill_hits}


def build_search_text(job):
    parts = [
        job.get("title") or "",
        job.get("company") or "",
        job.get("location") or "",
        " ".join(job.get("tags") or []),
        job.get("description") or "",
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).lower()[:900]


def loose_key(job):
    def norm(value):
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    company = norm(job.get("company"))
    title = norm(job.get("title"))
    location = norm(job.get("location"))
    if len(company) < 2 or len(title) < 4:
        return ""
    return f"{company}|{title}|{location}"


def make_job(title, company, location, remote, tags, url, apply_url, source, salary, published, description):
    url = (url or "").strip()
    apply_url = (apply_url or "").strip() or url
    if not url.lower().startswith(("http://", "https://")):
        return None
    if not apply_url.lower().startswith(("http://", "https://")):
        apply_url = url
    title = one_line(title, 180)
    company = one_line(company, 120) or "Unknown company"
    if not title:
        return None
    location = one_line(location, 120)
    remote_flag = bool(remote) or any(word in location.lower() for word in REMOTE_WORDS)
    if remote_flag and location and "remote" not in location.lower():
        location = f"{location} · Remote"
    elif remote_flag and not location:
        location = "Remote"
    description = plain_text(description or "", limit=5000)
    job = {
        "id": hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16],
        "title": title,
        "company": company,
        "location": location,
        "remote": remote_flag,
        "tags": clean_tags(tags),
        "url": url,
        "apply_url": apply_url,
        "source": source,
        "also_seen_on": [],
        "salary": clean_salary(salary),
        "published_ts": parse_ts(published),
        "description": description,
        "first_seen_at": now_iso(),
        "status": "new",
        "draft": None,
        "link_opened_at": None,
    }
    job["dedupe_key"] = loose_key(job)
    job["search_text"] = build_search_text(job)
    return job


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(str(exc.reason or exc)) from exc
    except TimeoutError as exc:
        raise FetchError("timed out") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FetchError("response was not JSON") from exc


def fetch_remotive():
    data = fetch_json("https://remotive.com/api/remote-jobs")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        job = make_job(
            title=item.get("title"),
            company=item.get("company_name"),
            location=item.get("candidate_required_location") or "Remote",
            remote=True,
            tags=item.get("tags") or [],
            url=item.get("url"),
            apply_url=item.get("url"),
            source="Remotive",
            salary=item.get("salary"),
            published=item.get("publication_date"),
            description=item.get("description"),
        )
        if job:
            jobs.append(job)
    return jobs


def fetch_arbeitnow():
    url = "https://www.arbeitnow.com/api/job-board-api"
    jobs = []
    for _ in range(2):
        data = fetch_json(url)
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            job = make_job(
                title=item.get("title"),
                company=item.get("company_name"),
                location=item.get("location"),
                remote=bool(item.get("remote")),
                tags=item.get("tags") or item.get("job_types") or [],
                url=item.get("url"),
                apply_url=item.get("url"),
                source="Arbeitnow",
                salary="",
                published=item.get("created_at"),
                description=item.get("description"),
            )
            if job:
                jobs.append(job)
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return jobs


def fetch_remoteok():
    data = fetch_json("https://remoteok.com/api")
    jobs = []
    if not isinstance(data, list):
        raise FetchError("unexpected Remote OK payload")
    for item in data:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        job = make_job(
            title=item.get("position"),
            company=item.get("company"),
            location=item.get("location") or "Remote",
            remote=True,
            tags=item.get("tags") or [],
            url=item.get("url") or item.get("apply_url"),
            apply_url=item.get("apply_url") or item.get("url"),
            source="Remote OK",
            salary=money_range(item.get("salary_min"), item.get("salary_max")),
            published=item.get("date") or item.get("epoch"),
            description=item.get("description"),
        )
        if job:
            jobs.append(job)
    return jobs


def fetch_jobicy():
    data = fetch_json("https://jobicy.com/api/v2/remote-jobs?count=50")
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        salary = money_range(item.get("salaryMin"), item.get("salaryMax"))
        if salary and item.get("salaryCurrency") and item.get("salaryCurrency") != "USD":
            salary = f"{salary} {item.get('salaryCurrency')}"
        job = make_job(
            title=item.get("jobTitle"),
            company=item.get("companyName"),
            location=item.get("jobGeo") or "Remote",
            remote=True,
            tags=[item.get("jobIndustry"), item.get("jobLevel"), item.get("jobType")],
            url=item.get("url"),
            apply_url=item.get("url"),
            source="Jobicy",
            salary=salary,
            published=item.get("pubDate"),
            description=item.get("jobDescription") or item.get("jobExcerpt"),
        )
        if job:
            jobs.append(job)
    return jobs


FETCHERS = {
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
    "remoteok": fetch_remoteok,
    "jobicy": fetch_jobicy,
}


def is_due(info, interval, now):
    if not info:
        return True
    success = float(info.get("fetched_ts") or 0)
    attempt = float(info.get("attempted_ts") or 0)
    if attempt and now - attempt < 90:
        return False
    if success and now - success < interval:
        return False
    if attempt > success and now - attempt < 15 * 60:
        return False
    return True


def skip_text(source_id, info, now):
    spec = SOURCES[source_id]
    success = float((info or {}).get("fetched_ts") or 0)
    if success:
        minutes = max(1, int((now - success) / 60))
        return f"{spec['label']} ({spec['policy']}; last check {minutes} min ago)"
    return f"{spec['label']} ({spec['policy']}; waiting to retry)"


def note_also_seen(existing, source):
    if source == existing.get("source"):
        return
    others = [name for name in existing.get("also_seen_on") or [] if name != existing.get("source")]
    if source not in others:
        others.append(source)
    existing["also_seen_on"] = others


def merge_existing(existing, incoming):
    note_also_seen(existing, incoming["source"])
    if incoming.get("published_ts", 0) >= existing.get("published_ts", 0):
        for field in ("title", "company", "location", "url", "apply_url", "salary", "description", "tags"):
            if incoming.get(field):
                existing[field] = incoming[field]
        existing["published_ts"] = incoming.get("published_ts") or existing.get("published_ts") or 0
    existing["remote"] = bool(existing.get("remote") or incoming.get("remote"))
    existing["search_text"] = build_search_text(existing)
    if not existing.get("dedupe_key"):
        existing["dedupe_key"] = loose_key(existing)


def trim_jobs(store):
    jobs = store["jobs"]
    if len(jobs) <= MAX_JOBS:
        return
    rank = {"skipped": 0, "applied": 1, "new": 2, "drafted": 3, "ready": 4}

    def sort_key(job):
        return (rank.get(job.get("status"), 2), job.get("first_seen_at") or "")

    for job in sorted(jobs.values(), key=sort_key):
        if len(jobs) <= MAX_JOBS:
            break
        if job.get("status") in {"drafted", "ready"}:
            continue
        jobs.pop(job.get("id"), None)


def integrate(store, fetched):
    jobs = store["jobs"]
    loose = {}
    for job in jobs.values():
        key = job.get("dedupe_key") or ""
        if key:
            loose.setdefault(key, job["id"])
    added = []
    for incoming in fetched:
        current = jobs.get(incoming["id"])
        if current:
            merge_existing(current, incoming)
            continue
        key = incoming.get("dedupe_key") or ""
        if key and key in loose and loose[key] in jobs:
            note_also_seen(jobs[loose[key]], incoming["source"])
            continue
        jobs[incoming["id"]] = incoming
        added.append(incoming["id"])
        if key:
            loose[key] = incoming["id"]
    trim_jobs(store)
    added = [job_id for job_id in added if job_id in jobs]
    return added


def refresh():
    now = time.time()
    with locked():
        store = load_store()
        due = []
        skipped = []
        for source_id, spec in SOURCES.items():
            info = store["sources"].get(source_id) or {}
            if is_due(info, spec["min_interval"], now):
                due.append(source_id)
                info = dict(info)
                info["attempted_ts"] = now
                store["sources"][source_id] = info
            else:
                skipped.append(skip_text(source_id, info, now))
        save_store(store)

    fetched = {}
    errors = []
    if due:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(FETCHERS[source_id]): source_id for source_id in due}
            for future in as_completed(futures):
                source_id = futures[future]
                try:
                    fetched[source_id] = future.result()
                except Exception as exc:  # noqa: BLE001 — report each source, keep going
                    errors.append(f"{SOURCES[source_id]['label']}: {exc}")

    finished = time.time()
    with locked():
        store = load_store()
        added_ids = []
        for source_id, jobs in fetched.items():
            info = dict(store["sources"].get(source_id) or {})
            info["fetched_ts"] = finished
            info["attempted_ts"] = finished
            info["error"] = None
            info["last_count"] = len(jobs)
            new_ids = integrate(store, jobs)
            info["last_added"] = len(new_ids)
            store["sources"][source_id] = info
            added_ids.extend(new_ids)
        for message in errors:
            label = message.split(":", 1)[0]
            source_id = next((key for key, spec in SOURCES.items() if spec["label"] == label), None)
            if not source_id:
                continue
            info = dict(store["sources"].get(source_id) or {})
            info["attempted_ts"] = finished
            info["error"] = message.split(":", 1)[-1].strip()
            store["sources"][source_id] = info
        if fetched:
            store["last_refresh_at"] = now_iso()
            store["last_new_ids"] = added_ids
        save_store(store)

    added = len(added_ids) if fetched else 0
    if added:
        message = f"Found {added} new posting{'s' if added != 1 else ''}."
    elif fetched:
        message = "No new postings in the sources checked just now."
    elif skipped:
        message = "No source was due for another check yet."
    else:
        message = "Could not reach the public job feeds."
    if skipped:
        message += " Skipped: " + "; ".join(skipped) + "."
    if errors:
        message += " Problems: " + "; ".join(errors) + "."
    message += " Nothing was submitted."
    return {
        "ok": bool(fetched or skipped),
        "added": added,
        "fetched": [SOURCES[source_id]["label"] for source_id in fetched],
        "skipped": skipped,
        "errors": errors,
        "message": message,
    }


def build_draft(job, profile, skill_hits):
    name = (profile.get("name") or "").strip() or "Your Name"
    contact = (profile.get("contact") or "").strip()
    summary = (profile.get("summary") or "").strip()
    if summary.startswith("Replace this with"):
        summary = "[Edit your profile summary before sending.] " + summary
    company = job.get("company") or "the hiring team"
    title = job.get("title") or "this role"
    if skill_hits:
        skills_text = ", ".join(skill_hits[:5])
        skill_sentence = f"This posting stood out because it connects to my experience with {skills_text}."
    else:
        skills_text = "the skills I will add on my profile"
        skill_sentence = (
            "I still need to line up concrete skills from my profile with this posting before I send it."
        )
    preference = profile.get("location") or "my preferred location"
    job_location = job.get("location") or "the location on the posting"
    salary = job.get("salary") or ""
    pay_sentence = f" The posting lists pay as: {salary}." if re.search(r"\d", salary) else ""
    sign_off = name if not contact else f"{name}\n{contact}"
    cover = (
        f"Hello {company} team,\n\n"
        f"I am applying for the {title} role. {summary}\n\n"
        f"{skill_sentence} I am focused on {preference} roles and can talk through relevant work in a first conversation.\n\n"
        f"Thank you for your time,\n{sign_off}"
    )
    answers = [
        {
            "question": "Why this role?",
            "answer": (
                f"I am applying for {title} at {company} because it matches the kind of work I want. "
                f"{skill_sentence}{pay_sentence}"
            ),
        },
        {
            "question": "Relevant skills",
            "answer": (
                f"Skills from my profile that appear in this posting: {skills_text}."
                if skill_hits
                else "None of the skills on my profile were found in the posting text. Edit your skills, generate the note again, and add a real example before you submit."
            ),
        },
        {
            "question": "Location",
            "answer": f"My location preference is {preference}. This posting lists {job_location}. Confirm this still fits before you submit.",
        },
    ]
    return cover, answers


def public_job(job, profile, detail=False):
    match = score_job(job, profile)
    payload = {
        "id": job["id"],
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "remote": bool(job.get("remote")),
        "tags": job.get("tags") or [],
        "url": job.get("url") or "",
        "apply_url": job.get("apply_url") or job.get("url") or "",
        "source": job.get("source") or "",
        "also_seen_on": job.get("also_seen_on") or [],
        "salary": job.get("salary") or "",
        "published_ts": job.get("published_ts") or 0,
        "first_seen_at": job.get("first_seen_at"),
        "status": job.get("status") or "new",
        "score": match["score"],
        "match_reasons": match["reasons"],
        "link_opened_at": job.get("link_opened_at"),
        "has_draft": bool(job.get("draft")),
        "search_text": build_search_text(job),
    }
    if detail:
        payload["description"] = job.get("description") or ""
        payload["draft"] = job.get("draft")
        payload["warnings"] = profile_warnings(profile)
    return payload


def counts_for(store):
    counts = {status: 0 for status in STATUSES}
    for job in store["jobs"].values():
        status = job.get("status") or "new"
        counts[status] = counts.get(status, 0) + 1
    counts["all"] = len(store["jobs"])
    return counts


def public_sources(store):
    listed = []
    for source_id, spec in SOURCES.items():
        info = store["sources"].get(source_id) or {}
        listed.append(
            {
                "id": source_id,
                "label": spec["label"],
                "home": spec["home"],
                "fetched_ts": info.get("fetched_ts"),
                "error": info.get("error"),
                "last_count": info.get("last_count"),
                "last_added": info.get("last_added"),
                "policy": spec["policy"],
            }
        )
    return listed


def state_from(store, profile):
    jobs = [public_job(job, profile, detail=False) for job in store["jobs"].values()]
    jobs.sort(key=lambda item: (item.get("published_ts") or 0, item.get("score") or 0), reverse=True)
    return {
        "profile": profile,
        "jobs": jobs,
        "warnings": profile_warnings(profile),
        "meta": {
            "last_refresh_at": store.get("last_refresh_at"),
            "last_new_ids": store.get("last_new_ids") or [],
            "counts": counts_for(store),
            "sources": public_sources(store),
            "total": len(store["jobs"]),
        },
    }


def current_state():
    with locked():
        return state_from(load_store(), load_profile())


def valid_id(job_id):
    return bool(re.fullmatch(r"[a-f0-9]{16}", job_id or ""))


def update_profile(payload):
    with locked():
        profile = save_profile(payload)
        return state_from(load_store(), profile)


def job_detail(job_id):
    if not valid_id(job_id):
        return None
    with locked():
        store = load_store()
        job = store["jobs"].get(job_id)
        if not job:
            return None
        return public_job(job, load_profile(), detail=True)


def generate_draft(job_id):
    if not valid_id(job_id):
        return None
    with locked():
        store = load_store()
        profile = load_profile()
        job = store["jobs"].get(job_id)
        if not job:
            return None
        match = score_job(job, profile)
        cover, answers = build_draft(job, profile, match["skill_hits"])
        job["draft"] = {
            "cover_note": cover,
            "answers": answers,
            "warnings": profile_warnings(profile),
            "generated_at": now_iso(),
            "edited": False,
        }
        if job.get("status") in {"new", "skipped"}:
            job["status"] = "drafted"
        save_store(store)
        return public_job(job, profile, detail=True)


def save_draft(job_id, payload):
    if not valid_id(job_id):
        return None
    cover = payload.get("cover_note") if isinstance(payload, dict) else None
    if not isinstance(cover, str) or not cover.strip():
        raise ValueError("Write a cover note before saving the draft.")
    answers = []
    for item in (payload.get("answers") or [])[:6]:
        if not isinstance(item, dict):
            continue
        question = one_line(item.get("question"), 160) or "Answer"
        answer = plain_text(item.get("answer") or "", limit=2000)
        answers.append({"question": question, "answer": answer})
    with locked():
        store = load_store()
        profile = load_profile()
        job = store["jobs"].get(job_id)
        if not job:
            return None
        previous = job.get("draft") or {}
        job["draft"] = {
            "cover_note": cover.strip()[:5000],
            "answers": answers,
            "warnings": profile_warnings(profile),
            "generated_at": previous.get("generated_at") or now_iso(),
            "edited": True,
        }
        if job.get("status") in {"new", "skipped"}:
            job["status"] = "drafted"
        save_store(store)
        return public_job(job, profile, detail=True)


def set_status(job_id, status):
    if status not in STATUSES:
        raise ValueError("Unknown status.")
    if not valid_id(job_id):
        return None
    with locked():
        store = load_store()
        profile = load_profile()
        job = store["jobs"].get(job_id)
        if not job:
            return None
        if status in {"drafted", "ready"} and not job.get("draft"):
            raise ValueError("Save a draft before marking it drafted or ready.")
        job["status"] = status
        save_store(store)
        return public_job(job, profile, detail=True)


def mark_opened(job_id):
    if not valid_id(job_id):
        return None
    with locked():
        store = load_store()
        profile = load_profile()
        job = store["jobs"].get(job_id)
        if not job:
            return None
        job["link_opened_at"] = now_iso()
        save_store(store)
        return public_job(job, profile, detail=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "FreshRoles/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def _handle(self, method):
        try:
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if method == "GET" and path in {"/", "/index.html"}:
                self._send_file(STATIC / "index.html")
                return
            if method == "GET" and path.startswith("/static/"):
                relative = path[len("/static/") :]
                self._send_file(STATIC / relative)
                return
            if method == "GET" and path == "/api/state":
                self._send_json(200, current_state())
                return
            if method == "PUT" and path == "/api/profile":
                self._send_json(200, update_profile(self._read_json()))
                return
            if method == "POST" and path == "/api/profile/resume":
                result = resume_from_upload(
                    self.headers.get("Content-Type", ""),
                    self._read_body(8 * 1024 * 1024),
                )
                self._send_json(200, result)
                return
            if method == "POST" and path == "/api/refresh":
                report = refresh()
                payload = current_state()
                payload["refresh"] = report
                self._send_json(200 if report["ok"] else 502, payload)
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "jobs":
                job_id = parts[2]
                action = parts[3] if len(parts) > 3 else ""
                if method == "GET" and not action:
                    job = job_detail(job_id)
                    if job is None:
                        self._send_json(404, {"error": "That job is not in the local list."})
                    else:
                        self._send_json(200, job)
                    return
                if method == "POST" and action == "draft":
                    job = generate_draft(job_id)
                    if job is None:
                        self._send_json(404, {"error": "That job is not in the local list."})
                    else:
                        self._send_json(200, job)
                    return
                if method == "PUT" and action == "draft":
                    job = save_draft(job_id, self._read_json())
                    if job is None:
                        self._send_json(404, {"error": "That job is not in the local list."})
                    else:
                        self._send_json(200, job)
                    return
                if method == "POST" and action == "status":
                    body = self._read_json()
                    job = set_status(job_id, body.get("status"))
                    if job is None:
                        self._send_json(404, {"error": "That job is not in the local list."})
                    else:
                        self._send_json(200, job)
                    return
                if method == "POST" and action == "opened":
                    job = mark_opened(job_id)
                    if job is None:
                        self._send_json(404, {"error": "That job is not in the local list."})
                    else:
                        self._send_json(200, job)
                    return
            self._send_json(404, {"error": "Not found."})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "Something went wrong on this computer."})

    def _read_body(self, limit):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise ValueError("Upload a resume file.")
        if length > limit:
            raise ValueError("That file is too large. Keep it under 8 MB.")
        return self.rfile.read(length)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > 1_000_000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Send JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON object expected.")
        return data

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        root = STATIC.resolve()
        try:
            target = path.resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            self._send_json(404, {"error": "Not found."})
            return
        if not target.is_file():
            self._send_json(404, {"error": "Not found."})
            return
        kind = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def serve():
    import os

    host = "127.0.0.1"
    start = int(os.environ.get("FRESH_ROLES_PORT", "8765"))
    last_error = None
    ThreadingHTTPServer.allow_reuse_address = True
    for port in range(start, start + 5):
        try:
            httpd = ThreadingHTTPServer((host, port), Handler)
        except OSError as exc:
            last_error = exc
            continue
        httpd.daemon_threads = True
        print(f"Fresh Roles is running at http://{host}:{port}", flush=True)
        print("It drafts applications on this computer. It does not sign in or submit them.", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            httpd.server_close()
        return 0
    print(f"Could not start the server ({last_error}).", file=sys.stderr)
    return 1


def print_refresh(report):
    print(report["message"], flush=True)
    if report["fetched"]:
        print("Checked: " + ", ".join(report["fetched"]), flush=True)
    print(f"New posts stored: {report['added']}", flush=True)


def main(argv):
    command = argv[1] if len(argv) > 1 else "serve"
    if command in {"-h", "--help", "help"}:
        print("Usage: python3 app.py [serve|refresh]")
        print("  serve    start the local site (default)")
        print("  refresh  pull new public posts once, then exit")
        return 0
    if command == "refresh":
        report = refresh()
        print_refresh(report)
        return 0 if report["ok"] else 1
    if command == "serve":
        return serve()
    print("Usage: python3 app.py [serve|refresh]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
