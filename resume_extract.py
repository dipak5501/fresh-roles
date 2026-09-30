"""Read a resume on this computer and map it into profile fields.

PDF text uses pypdf. DOCX and plain text use the Python standard library.
The file bytes are not written to disk.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_DOCX_XML = 8 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 15 * 1024 * 1024

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

HEADER_MAP = {
    "summary": "summary",
    "professional summary": "summary",
    "career summary": "summary",
    "profile": "summary",
    "about": "summary",
    "about me": "summary",
    "objective": "summary",
    "career objective": "summary",
    "overview": "summary",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "work history": "experience",
    "relevant experience": "experience",
    "career history": "experience",
    "education": "education",
    "academic background": "education",
    "academics": "education",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "core competencies": "skills",
    "competencies": "skills",
    "technologies": "skills",
    "tech stack": "skills",
    "tools": "skills",
    "expertise": "skills",
    "links": "links",
    "websites": "links",
    "web": "links",
    "projects": "projects",
    "personal projects": "projects",
    "selected projects": "projects",
    "publications": "publications",
    "publication": "publications",
    "awards": "awards",
    "honors": "awards",
    "honors and awards": "awards",
    "certifications": "certifications",
    "certificates": "certifications",
}

STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA", "ID",
    "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC",
    "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY", "DC",
}

STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}

TITLE_WORD = re.compile(
    r"(?i)\b("
    r"engineer|developer|designer|manager|analyst|scientist|architect|consultant|"
    r"specialist|director|intern|founder|programmer|administrator|researcher|"
    r"coordinator|officer|associate|technician|accountant|recruiter|writer|editor|"
    r"devops|sre|marketing|sales|teacher|nurse|counselor|paralegal|attorney|lawyer|"
    r"supervisor|principal|assistant|representative|producer|strategist|administrator|tutor"
    r")\b"
)
WEAK_TITLE = re.compile(r"(?i)^(lead|staff|product|support|head)$")
ACTION_START = re.compile(
    r"(?i)^(built|designed|wrote|led|managed|developed|created|implemented|worked|"
    r"helped|owned|delivered|improved|maintained|supported|responsible|collaborated|"
    r"used|using|including|assisted)\b"
)
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z])"
)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]*)?(?:\(\s*\d{3}\s*\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
    r"|(?<!\d)\+\d{1,3}(?:[\s.\-]*\d){8,14}(?!\d)"
)
URL_RE = re.compile(r"https?://[^\s<>\[\]()\"']+", re.I)
BARE_URL_RE = re.compile(
    r"(?i)(?<![@/])\b((?:www\.)?(?:linkedin\.com|github\.com)/[A-Za-z0-9_./\-]+)"
)
CITY_ST = re.compile(r"\b([A-Z][a-zA-Z.]+(?:[ \-][A-Z][a-zA-Z.]+){0,3}),\s*([A-Z]{2})\b")
CITY_STATE_NAME = re.compile(
    r"\b([A-Z][a-zA-Z.]+(?:[ \-][A-Z][a-zA-Z.]+){0,3}),\s*"
    r"([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})\b"
)
LOCATION_LABEL = re.compile(
    r"(?i)^\s*(?:location|based in|located in|address)\s*[:\-]\s*(.+?)\s*$"
)
DATE_HINT = re.compile(r"(?i)\b((19|20)\d{2}|present|current)\b")
BULLET_RE = re.compile(r"^\s*(?:[-*\u2022\u25e6\u2043]|\d+[.)])\s+")

SKILL_TERMS = [
    "Python", "JavaScript", "TypeScript", "Java", "Golang", "Rust", "Ruby", "PHP",
    "C++", "C#", "SQL", "PostgreSQL", "Postgres", "MySQL", "MongoDB", "Redis",
    "Docker", "Kubernetes", "AWS", "GCP", "Azure", "React", "Vue", "Angular",
    "Node.js", "Django", "Flask", "FastAPI", "Git", "Linux", "HTML", "CSS",
    "GraphQL", "REST", "APIs", "Terraform", "Spark", "Pandas", "NumPy",
    "TensorFlow", "PyTorch", "Scala", "Kotlin", "Swift", "Selenium", "Jenkins",
    "CI/CD", "Kafka", "Elasticsearch", "Snowflake", "Airflow", "Tableau", "Figma",
    "Excel", "Salesforce", "Snowflake", "Microservices",
]
_SKILL_KEYS = {term.casefold() for term in SKILL_TERMS}

FIELD_ORDER = [
    "name",
    "email",
    "phone",
    "contact",
    "links",
    "target_titles",
    "skills",
    "keywords",
    "location",
    "summary",
    "experience",
    "education",
]
IMPORTANT = ("name", "target_titles", "skills", "location", "summary")
PREVIEW_LABELS = [
    ("name", "Name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("contact", "Contact line"),
    ("target_titles", "Target titles"),
    ("skills", "Skills"),
    ("location", "Location"),
    ("keywords", "Keywords"),
    ("summary", "Summary"),
    ("experience", "Experience"),
    ("education", "Education"),
    ("links", "Links"),
]


def clean_filename(filename):
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\r\n\x00]", "", name).strip()
    return name[:120]


def suffix_of(filename):
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Upload a PDF, DOCX, or plain text file (.txt or .md).")
    return suffix


def _disp_param(disposition, key):
    star = re.search(rf"{key}\*=([^']*)''([^;]+)", disposition, re.I)
    if star:
        return unquote(star.group(2).strip().strip('"'))
    match = re.search(rf'{key}="([^"]*)"|{key}=([^;\s]+)', disposition, re.I)
    if not match:
        return ""
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return raw.strip()


def parse_upload(content_type, body):
    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("Upload a resume file.")
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, re.I)
    if not match:
        raise ValueError("Upload a resume file.")
    boundary = (match.group(1) or match.group(2)).encode("utf-8", errors="ignore")
    marker = b"--" + boundary
    found = None
    for chunk in body.split(marker):
        if chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        header_blob, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        header_text = header_blob.decode("iso-8859-1", errors="replace")
        disposition = ""
        for line in header_text.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1].strip()
                break
        if "form-data" not in disposition.lower():
            continue
        if _disp_param(disposition, "name") != "resume":
            continue
        filename = clean_filename(_disp_param(disposition, "filename"))
        if filename:
            found = (filename, data)
    if not found:
        raise ValueError("Choose a resume file to upload.")
    filename, data = found
    if not data:
        raise ValueError("That file is empty.")
    return filename, data


def extract_docx(data):
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file does not look like a DOCX document.") from exc
    with archive:
        total = sum(info.file_size for info in archive.infolist())
        if total > MAX_DOCX_UNCOMPRESSED:
            raise ValueError("That document is too large to read.")
        names = archive.namelist()
        ordered = []
        for name in names:
            lower = name.lower()
            if lower.startswith("word/header") and lower.endswith(".xml"):
                ordered.append(name)
        if "word/document.xml" in names:
            ordered.append("word/document.xml")
        for name in names:
            lower = name.lower()
            if lower.startswith("word/footer") and lower.endswith(".xml"):
                ordered.append(name)
        if "word/document.xml" not in names:
            raise ValueError("That DOCX has no readable document text.")
        chunks = []
        for name in ordered:
            xml = archive.read(name)
            if len(xml) > MAX_DOCX_XML:
                raise ValueError("That document is too large to read.")
            chunks.append(_docx_xml_text(xml))
    return "\n".join(part for part in chunks if part).strip()


def _docx_xml_text(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("Could not read that DOCX document.") from exc
    paragraphs = []
    for paragraph in root.iter(W_NS + "p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == W_NS + "t" and node.text:
                pieces.append(node.text)
            elif node.tag == W_NS + "tab":
                pieces.append(" ")
            elif node.tag == W_NS + "br":
                pieces.append("\n")
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "PDF reading needs pypdf. Run: python3 -m pip install -r requirements.txt"
        ) from exc
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise ValueError("Could not read that PDF.") from exc
    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise ValueError("That PDF is password-protected, so its text was not read.") from exc
        if unlocked == 0:
            raise ValueError("That PDF is password-protected, so its text was not read.")
    pages = list(reader.pages[:20])
    parts = []
    for page in pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts).strip(), len(reader.pages), _pdf_uris(reader)


def _pdf_uris(reader):
    found = []
    seen = set()
    for page in list(reader.pages[:20]):
        try:
            annots = page.get("/Annots") or []
        except Exception:
            continue
        for annot in annots:
            try:
                obj = annot.get_object()
                action = obj.get("/A")
                uri = action.get("/URI") if action is not None else None
            except Exception:
                continue
            if not uri:
                continue
            url = str(uri).strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            key = url.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(url)
            if len(found) >= 12:
                return found
    return found


def decode_plain(data):
    if data.startswith(b"%PDF") or data.startswith(b"PK"):
        raise ValueError("That file does not look like plain text. Use the real PDF or DOCX extension.")
    if b"\x00" in data[:8192]:
        raise ValueError("That file does not look like plain text.")
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("That file does not look like plain text.")


def read_document(filename, data):
    suffix = suffix_of(filename)
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ValueError("That file does not look like a PDF.")
        text, pages, links = extract_pdf(data)
        return text, pages, links
    if suffix == ".docx":
        if not data.startswith(b"PK"):
            raise ValueError("That file does not look like a DOCX document.")
        return extract_docx(data), None, []
    return decode_plain(data), None, []


def _clean_line(line):
    text = line.strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    return text.strip()


def _header_key(line):
    text = _clean_line(line).lower()
    text = re.sub(r"[:\-–—|]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text or len(text) > 48:
        return None
    return HEADER_MAP.get(text)


def _split_sections(text):
    sections = {"header": []}
    order = ["header"]
    current = "header"
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_line(raw)
        if not line:
            sections.setdefault(current, []).append("")
            continue
        key = _header_key(line)
        if key:
            current = key
            if current not in sections:
                order.append(current)
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _letters(text):
    return sum(1 for char in text if char.isalpha())


def is_thin(text):
    compact = re.sub(r"\s+", "", text or "")
    letters = _letters(compact)
    if letters < 40:
        return True
    if compact and letters / len(compact) < 0.25 and letters < 80:
        return True
    return False


def _is_bullet(line):
    return bool(BULLET_RE.match(line or ""))


def _words(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9+#.]*", text or "")


def _display_name(name):
    parts = name.split()
    if name.isupper() and parts:
        return " ".join(part[:1] + part[1:].lower() for part in parts)
    return name


def _strip_glued_place(text):
    parts = text.split()
    if len(parts) < 3:
        return text
    last = parts[-1].strip(",.")
    prev = parts[-2].strip(",.")
    if len(last) < 4 or not last.isalpha() or last.isupper() or not last[:1].isupper():
        return text
    if TITLE_WORD.fullmatch(last) or last.casefold() in _SKILL_KEYS:
        return text
    if prev.casefold() not in _SKILL_KEYS and not TITLE_WORD.fullmatch(prev):
        return text
    rest = " ".join(parts[:-1]).strip()
    if _could_be_title(rest):
        return rest
    return text


def _title_segments(line):
    text = _clean_line(line)
    text = re.sub(r"^\s*(?:[-*\u2022]+\s*)", "", text)
    text = re.sub(
        r"(?i)\s*[\|•·,–—-]\s*.*\b((19|20)\d{2}|present|current)\b.*$",
        "",
        text,
    ).strip(" |-–—·•")
    text = re.sub(r"\s*\([^)]*\)", "", text)
    segments = re.split(r"\s+[•·]\s+|\s+\|\s+", text)
    cleaned = []
    for segment in segments:
        segment = _strip_glued_place(re.sub(r"\s+", " ", segment).strip(" |-–—"))
        if segment:
            cleaned.append(segment)
    return cleaned


def _could_be_title(text):
    words = _words(text)
    if not 1 <= len(words) <= 6 or len(text) > 70:
        return False
    if text.count(",") >= 2 or ACTION_START.match(text):
        return False
    if not TITLE_WORD.search(text):
        return False
    if len(words) == 1 and WEAK_TITLE.match(words[0]):
        return False
    if DATE_HINT.search(text) and not TITLE_WORD.search(re.sub(DATE_HINT, "", text)):
        return False
    return True


def _is_date_line(line):
    text = _clean_line(line)
    if not text or len(text) > 48 or _is_bullet(text):
        return False
    if TITLE_WORD.search(text):
        return False
    return bool(DATE_HINT.search(text))


def looks_like_name(line):
    text = _clean_line(line)
    if not text or len(text) > 48 or _header_key(text):
        return False
    if any(char.isdigit() for char in text):
        return False
    if any(token in text for token in ("@", "|", "/", "http")):
        return False
    if "," in text:
        return False
    parts = re.findall(r"[A-Za-z][A-Za-z'’.\-]*", text)
    parts = [part.strip(".") for part in parts if part.strip(".")]
    if not 2 <= len(parts) <= 4:
        return False
    if TITLE_WORD.search(text):
        return False
    return all(part[0].isupper() for part in parts)


def _find_city(text):
    for match in CITY_ST.finditer(text or ""):
        city, state = match.group(1).strip(), match.group(2)
        if state in STATES and city.lower() not in {"suite", "po", "box", "ste"}:
            return f"{city}, {state}"
    for match in CITY_STATE_NAME.finditer(text or ""):
        city, region = match.group(1).strip(), match.group(2).strip()
        if region.lower() in STATE_NAMES and city.lower() not in {"suite", "po", "box"}:
            return f"{city}, {region}"
    return ""


def _find_location(header_lines, all_lines):
    for line in all_lines:
        labeled = LOCATION_LABEL.match(line or "")
        if not labeled:
            continue
        value = labeled.group(1).strip(" |-–—")
        city = _find_city(value)
        if city:
            return city
        if re.fullmatch(r"(?i)remote|hybrid", value):
            return value.title()
        if value and len(value) <= 80 and len(_words(value)) <= 6:
            return value
    header = "\n".join(header_lines)
    city = _find_city(header)
    if city:
        return city
    if re.search(r"(?i)(?<![A-Za-z])remote(?![A-Za-z])", header):
        return "Remote"
    return ""


def _parse_skill_items(lines):
    blob = "\n".join(lines or [])
    parts = re.split(r"[\n,;|•·∙]+", blob)
    found = []
    seen = set()
    for part in parts:
        item = re.sub(r"^\s*[-*\u2022]+\s*", "", part).strip(" .-–—")
        if ":" in item:
            left, right = item.split(":", 1)
            if len(_words(left)) <= 3 and right.strip():
                item = right.strip()
        item = re.sub(r"\s+", " ", item).strip()
        if not item or len(item) > 40 or len(item) < 2:
            continue
        words = _words(item)
        if not words or len(words) > 4:
            continue
        if ". " in item or _header_key(item) or EMAIL_RE.search(item):
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(item)
        if len(found) >= 40:
            break
    return found


def _lexicon_skills(text):
    found = []
    seen = set()
    for term in SKILL_TERMS:
        pattern = rf"(?<![A-Za-z0-9+#]){re.escape(term)}(?![A-Za-z0-9+#])"
        if re.search(pattern, text, flags=re.I):
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(term)
        if len(found) >= 40:
            break
    return found


def _collect_titles(lines):
    found = []
    seen = set()
    for line in lines:
        if not line or _is_bullet(line) or _is_date_line(line):
            continue
        if "@" in line or "http" in line.lower():
            continue
        for text in _title_segments(line):
            if not _could_be_title(text):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(text)
            if len(found) >= 6:
                return found
    return found


def _paragraph(lines, limit):
    parts = []
    for line in lines or []:
        cleaned = re.sub(r"^\s*[-*\u2022]+\s*", "", line).strip()
        if cleaned:
            parts.append(cleaned)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def _block(lines, limit):
    kept = []
    for line in lines or []:
        kept.append(line.rstrip())
    text = "\n".join(kept).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def _merge_links(*groups):
    found = []
    seen = set()
    for group in groups:
        for raw in group or []:
            url = str(raw).strip().rstrip(".,);")
            if url.lower().startswith("www."):
                url = "https://" + url
            if not url.lower().startswith(("http://", "https://")):
                continue
            key = url.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(url)
            if len(found) >= 12:
                return found
    return found


def _links_in(text):
    urls = [match.group(0) for match in URL_RE.finditer(text or "")]
    urls.extend("https://" + match.group(1) for match in BARE_URL_RE.finditer(text or ""))
    return _merge_links(urls)


def _keywords(skills):
    found = []
    seen = set()
    for skill in skills:
        text = re.sub(r"\s+", " ", skill).strip().lower()
        if text and text not in seen:
            seen.add(text)
            found.append(text)
        if len(found) >= 40:
            break
    return found


def _empty_fields():
    return {
        "name": "",
        "email": "",
        "phone": "",
        "contact": "",
        "links": [],
        "target_titles": [],
        "skills": [],
        "keywords": [],
        "location": "",
        "summary": "",
        "experience": "",
        "education": "",
    }


def parse_profile(text):
    sections = _split_sections(text)
    header = [line for line in sections.get("header", []) if line]
    all_lines = []
    for lines in sections.values():
        all_lines.extend(lines)

    name = ""
    for line in header or all_lines[:12]:
        if looks_like_name(line):
            name = _display_name(_clean_line(line))
            break

    email_match = EMAIL_RE.search(text or "")
    email = email_match.group(0) if email_match else ""
    phone_match = PHONE_RE.search(text or "")
    phone = re.sub(r"\s+", " ", phone_match.group(0)).strip() if phone_match else ""
    links = _links_in(text)
    location = _find_location(header, all_lines)

    skill_lines = sections.get("skills") or []
    skills = _parse_skill_items(skill_lines) if any(line.strip() for line in skill_lines) else []
    if not skills:
        skills = _lexicon_skills(text)

    title_lines = list(header)
    if "experience" in sections:
        title_lines.extend(sections.get("experience") or [])
    else:
        for key, lines in sections.items():
            if key in {"skills", "education", "links", "header", "projects", "publications", "awards", "certifications"}:
                continue
            title_lines.extend(lines)
    titles = _collect_titles(title_lines)

    summary = _paragraph(sections.get("summary"), 1000)
    if not summary:
        sentences = []
        for line in header:
            if line == name or "@" in line or line.lower().startswith("http"):
                continue
            if _could_be_title(line) or _find_city(line) == line:
                continue
            if len(line) >= 50 or line.endswith("."):
                sentences.append(line)
        summary = _paragraph(sentences, 600)

    experience = _block(sections.get("experience"), 4000) if "experience" in sections else ""
    education = _block(sections.get("education"), 2000) if "education" in sections else ""
    contact = " · ".join(bit for bit in (email, phone) if bit)

    fields = _empty_fields()
    fields.update(
        {
            "name": name,
            "email": email,
            "phone": phone,
            "contact": contact,
            "links": links,
            "target_titles": titles,
            "skills": skills,
            "keywords": _keywords(skills),
            "location": location,
            "summary": summary,
            "experience": experience,
            "education": education,
        }
    )
    return fields


def _nonempty(value):
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def _short(value, limit=160):
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value if str(item).strip())
    else:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _human_join(items):
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _message(filename, chars, pages, thin, filled, missing):
    read = f"Read {chars:,} character{'s' if chars != 1 else ''}"
    if pages:
        read += f" across {pages} page{'s' if pages != 1 else ''}"
    read += f" from {filename} on this computer."
    if thin:
        return (
            read
            + " Almost no text was found, so no name or skills were filled in."
            + " A scanned PDF often has no text layer. Try a text-based PDF, DOCX, or TXT file."
        )
    labels = {
        "name": "name",
        "email": "email",
        "phone": "phone",
        "contact": "contact line",
        "links": "links",
        "target_titles": "target titles",
        "skills": "skills",
        "location": "location",
        "summary": "summary",
        "experience": "experience",
        "education": "education",
    }
    filled_labels = [labels[key] for key in labels if key in filled]
    text = read + " Filled " + (_human_join(filled_labels) or "nothing") + "."
    missing_labels = [labels[key] for key in IMPORTANT if key in missing]
    if missing_labels:
        text += " Not found: " + _human_join(missing_labels) + ". Those fields were left as they are."
    text += " Review every field below. You can edit them, and they save with the profile."
    return text


def extract_resume(filename, data):
    safe_name = clean_filename(filename) or "resume"
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("That file is too large. Keep it under 8 MB.")
    text, pages, extra_links = read_document(safe_name, data)
    text = text.replace("\x00", " ").strip()
    chars = len(text)
    preview = text[:800]
    thin = is_thin(text)
    if thin:
        fields = _empty_fields()
        filled = []
    else:
        fields = parse_profile(text)
        fields["links"] = _merge_links(extra_links, fields.get("links"))
        filled = [key for key in FIELD_ORDER if _nonempty(fields.get(key))]
    missing = [key for key in IMPORTANT if key not in filled]
    field_preview = []
    for key, label in PREVIEW_LABELS:
        if key not in filled:
            continue
        field_preview.append(f"{label}: {_short(fields.get(key))}")
    return {
        "filename": safe_name,
        "chars": chars,
        "pages": pages,
        "thin": thin,
        "message": _message(safe_name, chars, pages, thin, filled, missing),
        "preview": preview,
        "field_preview": field_preview,
        "filled": filled,
        "missing": missing,
        "fields": fields,
    }


def resume_from_upload(content_type, body):
    filename, data = parse_upload(content_type, body)
    return extract_resume(filename, data)
