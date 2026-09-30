"""Match a job to the local profile and draft tailored notes.

The numeric score stays a simple overlap count. Gaps are skills the posting
emphasizes that the profile does not list. Skills that are only on the profile
are not gaps. Wording can come from a local model; invented facts are dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import local_ai

_TERM = re.compile(r"[a-z0-9+#.]{2,}")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

LEXICON = [
    "Python", "Java", "JavaScript", "TypeScript", "C#", "C++", "Ruby", "PHP", "Kotlin",
    "Swift", "Scala", "Rust", "Golang", "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis",
    "SQLite", "Oracle", "Snowflake", "BigQuery", "React", "Vue", "Angular", "Node.js",
    "Django", "Flask", "FastAPI", "Spring Boot", "Spring", ".NET", "Next.js", "Express",
    "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "Ansible", "Linux", "Git",
    "CI/CD", "Jenkins", "GitHub Actions", "HTML", "CSS", "Sass", "GraphQL", "REST", "gRPC",
    "Kafka", "RabbitMQ", "Elasticsearch", "Spark", "Hadoop", "Airflow", "Pandas", "NumPy",
    "TensorFlow", "PyTorch", "scikit-learn", "Machine Learning", "Deep Learning", "NLP",
    "Tableau", "Power BI", "Excel", "Figma", "Salesforce", "SAP", "Microservices", "JWT",
    "OAuth", "Selenium", "Playwright", "Cypress", "dbt", "Looker", "Redshift", "Databricks",
    "Android", "iOS", "React Native", "Flutter", "Statistics", "Data Analysis", "Curriculum",
    "Teaching", "RStudio", "PySpark", "Apache Spark",
]

AMBIGUOUS = {
    "spring", "express", "spark", "flask", "swift", "ruby", "excel", "oracle", "sap",
    "git", "linux", "teaching", "statistics", "curriculum", "data analysis", "java",
}

ALIAS = {
    "js": "javascript",
    "javascript": "javascript",
    "node": "node.js",
    "nodejs": "node.js",
    "node.js": "node.js",
    "reactjs": "react",
    "react.js": "react",
    "react": "react",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "golang": "golang",
    "go": "golang",
    "ts": "typescript",
    "typescript": "typescript",
    "py": "python",
    "python": "python",
    "mongo": "mongodb",
    "mongodb": "mongodb",
    "sklearn": "scikit-learn",
    "scikit-learn": "scikit-learn",
    "powerbi": "power bi",
    "power bi": "power bi",
    "ci/cd": "ci/cd",
    "rest": "rest",
    "rest api": "rest",
    "rest apis": "rest",
    "apis": "rest",
    "api": "rest",
    "nextjs": "next.js",
    "next.js": "next.js",
    "vuejs": "vue",
    "vue.js": "vue",
    "react native": "react native",
    "c#": "c#",
    "c++": "c++",
    ".net": ".net",
    "dotnet": ".net",
    "gcp": "gcp",
    "google cloud": "gcp",
    "amazon web services": "aws",
    "aws": "aws",
    "azure": "azure",
    "ml": "machine learning",
    "machine learning": "machine learning",
}

HEADINGS = {
    "requirements",
    "qualifications",
    "required qualifications",
    "required skills",
    "what you bring",
    "what you'll need",
    "what you will need",
    "what we are looking for",
    "what we're looking for",
    "must have",
    "nice to have",
    "preferred qualifications",
    "basic qualifications",
    "minimum qualifications",
    "skills",
    "tech stack",
}

CUES = (
    "required",
    "requirements",
    "qualification",
    "must have",
    "must-have",
    "you have",
    "you'll have",
    "you will have",
    "experience with",
    "experience in",
    "proficient",
    "familiar with",
    "hands-on",
    "nice to have",
    "preferred",
    "bonus",
    "we need",
    "looking for",
    "minimum",
)

DEGREE_MARKERS = (
    "ph.d",
    "ph.d.",
    "phd",
    "doctorate",
    "mba",
    "bachelor",
    "master's",
    "masters",
    "master of",
    "b.s.",
    "m.s.",
    "b.a.",
    "m.a.",
    "bsc",
    "msc",
    "associate degree",
)

SYSTEM = (
    "You compare one job with a person's profile for a local job desk. "
    "Use only the facts in the user message. Do not invent employers, dates, "
    "degrees, schools, or skills. A gap is a skill the job emphasizes that is "
    "missing from PROFILE SKILLS. A skill that is only on the profile is not a gap. "
    "Return one JSON object and no other text. Keys: fit (array of short strings), "
    "gaps (array of skill names from ALLOWED GAPS only), resume_summary (string, "
    "2 to 4 sentences), bullet_emphasis (array of strings copied from EXPERIENCE LINES), "
    "cover_note (string, a short note to the hiring company). Do not claim a gap skill."
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def profile_fp(profile):
    keys = (
        "name",
        "contact",
        "target_titles",
        "skills",
        "location",
        "keywords",
        "summary",
        "experience",
        "education",
    )
    blob = json.dumps({key: profile.get(key) for key in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def fresh(job, profile):
    cached = job.get("insight")
    if not isinstance(cached, dict):
        return False
    if cached.get("profile_fp") != profile_fp(profile):
        return False
    draft = cached.get("draft")
    return isinstance(draft, dict) and bool(draft.get("cover_note") or draft.get("resume_summary"))


def engine_status():
    return local_ai.status()


def warmup():
    return local_ai.warmup()


def _canonical(term):
    text = re.sub(r"\s+", " ", (term or "").strip().lower())
    text = text.replace("node js", "node.js").replace("next js", "next.js")
    return ALIAS.get(text, text)


def _tokens(text):
    return set(_TERM.findall((text or "").lower()))


def _contains(blob, term):
    term = (term or "").lower().strip()
    if len(term) < 2 or not blob:
        return False
    pattern = rf"(?<![a-z0-9+#]){re.escape(term)}(?![a-z0-9+#])"
    return re.search(pattern, blob) is not None


def _profile_terms(profile):
    found = []
    seen = set()
    for item in list(profile.get("skills") or []) + list(profile.get("keywords") or []):
        key = _canonical(item)
        if key and key not in seen:
            seen.add(key)
            found.append(item)
    return found


def _covers(term, profile):
    want = _canonical(term)
    want_tokens = _tokens(want)
    if not want_tokens:
        return False
    for skill in _profile_terms(profile):
        got = _canonical(skill)
        if got == want:
            return True
        got_tokens = _tokens(got)
        if want_tokens <= got_tokens:
            return True
    return False


def _requirement_text(description):
    chunks = []
    capture = False
    for raw in (description or "").splitlines():
        stripped = raw.strip()
        key = re.sub(r"[:\-–—|]+$", "", stripped).strip().lower()
        if key in HEADINGS:
            capture = True
            continue
        if capture and not stripped:
            capture = False
            continue
        if capture:
            chunks.append(stripped)
            continue
        low = stripped.lower()
        if any(cue in low for cue in CUES):
            chunks.append(stripped)
    return "\n".join(chunks)


def _emphasized(job):
    description = job.get("description") or ""
    required = _requirement_text(description).lower()
    full = description.lower()
    found = []
    seen = set()

    def add(name):
        key = _canonical(name)
        if not key or key in seen:
            return
        seen.add(key)
        found.append(name)

    for tag in job.get("tags") or []:
        key = _canonical(tag)
        mapped = ALIAS.get(key)
        if mapped:
            # Display a lexicon-style name when we know it.
            display = next((item for item in LEXICON if _canonical(item) == mapped), tag)
            add(display)
            continue
        for item in LEXICON:
            if _canonical(item) == key:
                add(item)
                break

    for item in LEXICON:
        key = _canonical(item)
        blob_name = item.lower()
        in_required = _contains(required, blob_name) or _contains(required, key)
        if in_required:
            add(item)
            continue
        if key in AMBIGUOUS or blob_name in AMBIGUOUS:
            continue
        hits = len(re.findall(rf"(?<![a-z0-9+#]){re.escape(blob_name)}(?![a-z0-9+#])", full))
        if hits >= 2:
            add(item)
    return found


def _experience_lines(profile):
    lines = []
    for raw in (profile.get("experience") or "").splitlines():
        line = re.sub(r"^[\-\*\u2022]+\s*", "", raw.strip()).strip()
        if len(line) >= 24:
            lines.append(line)
    return lines


def _pick_bullets(lines, skill_hits):
    if not lines:
        return []
    ranked = []
    for line in lines:
        low = line.lower()
        hits = [skill for skill in skill_hits if _contains(low, skill)]
        if hits:
            ranked.append((len(hits), line))
    ranked.sort(key=lambda item: item[0], reverse=True)
    chosen = []
    for _, line in ranked:
        if line not in chosen:
            chosen.append(line)
        if len(chosen) >= 4:
            return chosen
    if chosen:
        return chosen
    return lines[:3]


def _clip(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def _heuristic_summary(job, profile, skill_hits):
    summary = _clip(profile.get("summary") or "", 700)
    title = job.get("title") or "this role"
    company = job.get("company") or "the company"
    if skill_hits:
        overlap = ", ".join(skill_hits[:5])
        extra = f" For the {title} role at {company}, the overlap with my profile is {overlap}."
    else:
        extra = f" For the {title} role at {company}, I am using only the background already on my profile."
    if summary.startswith("Replace this with"):
        return extra.strip()
    return (summary + extra).strip()


def _heuristic_cover(job, profile, skill_hits, bullets):
    title = job.get("title") or "this role"
    company = job.get("company") or "the hiring team"
    summary = _clip(profile.get("summary") or "", 500)
    if summary.startswith("Replace this with"):
        summary = "I still need to replace the example summary on my profile before I send this."
    if skill_hits:
        overlap = f"This posting overlaps with skills already on my profile: {', '.join(skill_hits[:5])}."
    else:
        overlap = "I am not claiming skills that this posting asks for unless they are already on my profile."
    proof = ""
    if bullets:
        proof = f" A line from my experience I would lead with: {bullets[0]}"
    name = (profile.get("name") or "").strip() or "Your Name"
    contact = (profile.get("contact") or "").strip()
    sign = name if not contact else f"{name}\n{contact}"
    return (
        f"Hello {company} team,\n\n"
        f"I am applying for the {title} role. {summary}\n\n"
        f"{overlap}{proof}\n\n"
        f"Thank you for your time,\n{sign}"
    )


def _gap_state(emphasized, profile):
    missing = []
    for term in emphasized:
        if _covers(term, profile):
            continue
        if term not in missing:
            missing.append(term)
    if not emphasized:
        note = "none_detected"
    elif not missing:
        note = "none_missing"
    else:
        note = "missing"
    return missing[:8], note


def heuristic(job, profile, match):
    skill_hits = list(match.get("skill_hits") or [])
    emphasized = _emphasized(job)
    gaps, gap_note = _gap_state(emphasized, profile)
    lines = _experience_lines(profile)
    bullets = _pick_bullets(lines, skill_hits)
    fit = list(match.get("reasons") or [])
    if not fit:
        fit = ["No strong overlap with your profile yet."]
    return {
        "score": int(match.get("score") or 0),
        "fit": fit[:5],
        "gaps": gaps,
        "gap_note": gap_note,
        "emphasized": emphasized[:12],
        "skill_hits": skill_hits,
        "resume_summary": _heuristic_summary(job, profile, skill_hits),
        "bullet_emphasis": bullets,
        "cover_note": _heuristic_cover(job, profile, skill_hits, bullets),
    }


def _profile_blob(profile):
    parts = [
        profile.get("name") or "",
        profile.get("summary") or "",
        profile.get("experience") or "",
        profile.get("education") or "",
        " ".join(profile.get("skills") or []),
        " ".join(profile.get("target_titles") or []),
        " ".join(profile.get("keywords") or []),
    ]
    return "\n".join(parts).lower()


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [part.strip() for part in parts if part and part.strip()]


def _ground_sentences(text, profile, job):
    blob = _profile_blob(profile)
    years = set(_YEAR.findall(blob))
    lexicon_keys = {_canonical(item) for item in LEXICON}
    kept = []
    for sentence in _sentences(text):
        low = sentence.lower()
        if any(year not in years for year in _YEAR.findall(sentence)):
            continue
        if any(marker in low and marker not in blob for marker in DEGREE_MARKERS):
            continue
        bad_skill = False
        for item in LEXICON:
            if _contains(low, item.lower()) and not _covers(item, profile):
                bad_skill = True
                break
        if bad_skill:
            continue
        if _invented_org(sentence, profile, job):
            continue
        # Drop claims that name a lexicon skill the person does not have,
        # including aliases already handled by _covers.
        if any(_canonical(token) in lexicon_keys and not _covers(token, profile) for token in _tokens(low)):
            # Token check is noisy (many normal words). Only the lexicon pass above counts.
            pass
        kept.append(sentence.strip())
    return " ".join(kept).strip()


def _invented_org(sentence, profile, job):
    blob = _profile_blob(profile)
    company = (job.get("company") or "").lower()
    for match in re.finditer(
        r"\b(?:at|for|with)\s+([A-Z][A-Za-z0-9&.'-]{2,}(?:\s+[A-Z][A-Za-z0-9&.'-]{1,}){0,3})",
        sentence,
    ):
        org = match.group(1).strip()
        low = org.lower()
        if low in blob or low in company or company in low:
            continue
        if low in {"the hiring team", "the team", "your team"}:
            continue
        return True
    return False


def _ground_bullets(candidates, lines):
    chosen = []
    for raw in candidates or []:
        if not isinstance(raw, str):
            continue
        bullet = raw.strip()
        if not bullet:
            continue
        best = _match_line(bullet, lines)
        if best and best not in chosen:
            chosen.append(best)
        if len(chosen) >= 4:
            break
    return chosen


def _match_line(bullet, lines):
    low = bullet.lower()
    tokens = _tokens(low)
    best = ""
    best_score = 0
    for line in lines:
        line_low = line.lower()
        if low in line_low or line_low in low:
            return line
        line_tokens = _tokens(line_low)
        if not tokens or not line_tokens:
            continue
        overlap = len(tokens & line_tokens)
        score = overlap / max(1, min(len(tokens), len(line_tokens)))
        if overlap >= 4 and score > best_score:
            best = line
            best_score = score
        elif score >= 0.62 and overlap >= 3 and score > best_score:
            best = line
            best_score = score
    return best


def _as_str_list(value, limit=6):
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
        if len(found) >= limit:
            break
    return found


def _apply_model(parsed, base, profile, job):
    lines = _experience_lines(profile)
    fit = []
    for line in _as_str_list(parsed.get("fit"), limit=5):
        grounded = _ground_sentences(line, profile, job)
        if grounded:
            fit.append(grounded)
    summary = _ground_sentences(parsed.get("resume_summary") or "", profile, job)
    cover = _ground_sentences(parsed.get("cover_note") or "", profile, job)
    bullets = _ground_bullets(_as_str_list(parsed.get("bullet_emphasis"), limit=6), lines)
    used = bool(summary and len(summary) >= 40) or bool(cover and len(cover) >= 80)
    if not used and not fit:
        return None
    if not fit:
        fit = base["fit"]
    if not summary or len(summary) < 40:
        summary = base["resume_summary"]
    else:
        # Keep the job identity even if the model omitted it.
        title = job.get("title") or ""
        company = job.get("company") or ""
        if title and title.lower() not in summary.lower():
            summary = f"For the {title} role at {company}, {summary[0].lower() + summary[1:] if summary else summary}"
    if not cover or len(cover) < 80:
        cover = base["cover_note"]
    else:
        cover = _sign(cover, profile)
    if not bullets:
        bullets = base["bullet_emphasis"]
    allowed = {_canonical(item): item for item in base["gaps"]}
    gaps = []
    for item in _as_str_list(parsed.get("gaps"), limit=8):
        key = _canonical(item)
        if key in allowed and allowed[key] not in gaps:
            gaps.append(allowed[key])
    if set(_canonical(item) for item in gaps) != set(allowed):
        gaps = list(base["gaps"])
    return {
        "fit": fit[:5],
        "gaps": gaps,
        "gap_note": base["gap_note"],
        "resume_summary": summary,
        "bullet_emphasis": bullets[:4],
        "cover_note": cover,
        "used_model": True,
    }


def _sign(text, profile):
    name = (profile.get("name") or "").strip()
    contact = (profile.get("contact") or "").strip()
    if name and name.lower() in text.lower():
        return text.strip()
    sign = name if not contact else f"{name}\n{contact}"
    if not sign:
        return text.strip()
    return text.rstrip() + "\n\n" + sign


def _pack(base, source, model, draft_fields=None):
    fields = draft_fields or base
    draft = {
        "cover_note": fields["cover_note"],
        "resume_summary": fields["resume_summary"],
        "bullet_emphasis": list(fields["bullet_emphasis"]),
        "fit": list(fields["fit"]),
        "gaps": list(fields["gaps"]),
        "answers": [
            {"question": "Why this role?", "answer": " ".join(fields["fit"][:3])},
            {
                "question": "Relevant skills",
                "answer": (
                    "Skills from the profile that show up here: " + ", ".join(base["skill_hits"][:8])
                    if base["skill_hits"]
                    else "No profile skill was found in the posting text."
                ),
            },
            {
                "question": "Gaps",
                "answer": _gap_sentence(fields["gaps"], fields["gap_note"]),
            },
        ],
        "source": source,
        "model": model,
        "warnings": [],
        "generated_at": now_iso(),
        "edited": False,
    }
    return {
        "profile_fp": "",
        "source": source,
        "model": model,
        "score": base["score"],
        "fit": list(fields["fit"]),
        "gaps": list(fields["gaps"]),
        "gap_note": fields["gap_note"],
        "draft": draft,
    }


def _gap_sentence(gaps, note):
    if note == "none_detected":
        return (
            "No clear required-skill list was found, so nothing was marked as a gap. "
            "Skills that are only on the profile are not gaps."
        )
    if note == "none_missing" or not gaps:
        return "No skill this posting emphasizes is missing from the profile."
    return "The posting emphasizes these, and they are not on the profile: " + ", ".join(gaps) + "."


def _prompt(job, profile, base):
    lines = _experience_lines(profile)
    experience = "\n".join(f"- {line}" for line in lines[:8]) or "(none listed)"
    description = (job.get("description") or "").strip()
    if len(description) > 1800:
        description = description[:1800].rsplit(" ", 1)[0]
    skills = ", ".join(profile.get("skills") or []) or "(none)"
    overlap = ", ".join(base["skill_hits"]) or "(none)"
    allowed = ", ".join(base["gaps"]) or "(none)"
    education = _clip(profile.get("education") or "", 500) or "(none listed)"
    summary = _clip(profile.get("summary") or "", 700) or "(none listed)"
    titles = ", ".join(profile.get("target_titles") or []) or "(none)"
    return (
        f"PROFILE NAME: {profile.get('name') or ''}\n"
        f"TARGET TITLES: {titles}\n"
        f"PROFILE SKILLS: {skills}\n"
        f"LOCATION: {profile.get('location') or ''}\n"
        f"SUMMARY:\n{summary}\n\n"
        f"EXPERIENCE LINES:\n{experience}\n\n"
        f"EDUCATION:\n{education}\n\n"
        f"JOB TITLE: {job.get('title') or ''}\n"
        f"COMPANY: {job.get('company') or ''}\n"
        f"JOB LOCATION: {job.get('location') or ''}\n"
        f"TAGS: {', '.join(job.get('tags') or [])}\n"
        f"OVERLAP SKILLS: {overlap}\n"
        f"ALLOWED GAPS: {allowed}\n"
        f"POSTING:\n{description}\n"
    )


def compose(job, profile, match, use_model=True):
    base = heuristic(job, profile, match)
    source = "builtin"
    model = ""
    fields = base
    if use_model:
        info = local_ai.status()
        parsed = None
        if info.get("available"):
            parsed = local_ai.complete(SYSTEM, _prompt(job, profile, base))
        if parsed:
            merged = _apply_model(parsed, base, profile, job)
            if merged:
                fields = {
                    "fit": merged["fit"],
                    "gaps": merged["gaps"],
                    "gap_note": merged["gap_note"],
                    "resume_summary": merged["resume_summary"],
                    "bullet_emphasis": merged["bullet_emphasis"],
                    "cover_note": merged["cover_note"],
                }
                source = "ollama"
                model = info.get("model") or ""
    packed = _pack(base, source, model, fields)
    packed["profile_fp"] = profile_fp(profile)
    return packed


def self_check():
    profile = {
        "name": "Test User",
        "contact": "",
        "target_titles": ["Backend Engineer"],
        "skills": ["Python", "React", "SQL"],
        "keywords": ["python"],
        "location": "Remote",
        "summary": "Builds internal tools with Python and React.",
        "experience": "- Built a Python service for lab scheduling data in 2022.\n- Created React screens for the same scheduling tool.",
        "education": "B.S. Computer Science, State University, 2020",
    }
    job = {
        "title": "Backend Engineer",
        "company": "Example Co",
        "location": "Remote",
        "tags": ["Python", "Kubernetes"],
        "description": (
            "Requirements:\n"
            "- Strong Python and SQL\n"
            "- Kubernetes experience is required\n"
            "\n"
            "We use AWS in production sometimes.\n"
        ),
    }
    match = {
        "score": 64,
        "reasons": ["Title aligns with “Backend Engineer”", "Skills mentioned: Python, SQL"],
        "skill_hits": ["Python", "SQL"],
    }
    built = compose(job, profile, match, use_model=False)
    gaps = built["gaps"]
    if "Kubernetes" not in gaps:
        raise AssertionError(gaps)
    if any("React" in gap for gap in gaps):
        raise AssertionError(gaps)
    if "Python" in gaps or "SQL" in gaps:
        raise AssertionError(gaps)
    if "AWS" in gaps:
        raise AssertionError(gaps)
    cover = built["draft"]["cover_note"]
    if "Kubernetes" in cover or "Google" in cover:
        raise AssertionError(cover)
    if "Example Co" not in cover or "Python" not in cover:
        raise AssertionError("cover missing grounded facts")
    for bullet in built["draft"]["bullet_emphasis"]:
        if bullet not in profile["experience"]:
            raise AssertionError(bullet)
    if built["source"] != "builtin":
        raise AssertionError(built["source"])
    if built["gap_note"] != "missing":
        raise AssertionError(built["gap_note"])
    return "ok"
