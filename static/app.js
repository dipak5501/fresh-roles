const STATUS_ORDER = [
  "new",
  "drafted",
  "ready",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "archived",
  "all",
];
const TRACKER = [
  ["new", "New"],
  ["drafted", "Drafted"],
  ["ready", "Ready"],
  ["applied", "Applied"],
  ["interviewing", "Interviewing"],
  ["offer", "Offer received"],
  ["rejected", "Rejected"],
  ["archived", "Archived"],
];

let jobs = [];
let detail = null;
let selectedId = null;
let statusFilter = "new";
let lastNewIds = new Set();
let listLimit = 40;
let loadingJobs = false;
let saveTimer = null;
let saveGen = 0;
let stateVersion = 0;
let booted = false;
let weekApplied = 0;
let draftDirty = false;

function h(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else if (key === "onkeydown") node.addEventListener("keydown", value);
    else if (key === "oninput") node.addEventListener("input", value);
    else if (key === "onchange") node.addEventListener("change", value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  const kids = children == null ? [] : Array.isArray(children) ? children : [children];
  for (const child of kids) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function band(score) {
  if (score >= 70) return "high";
  if (score >= 40) return "mid";
  return "low";
}

function formatWhen(ts) {
  if (!ts) return "Date unknown";
  const then = new Date(ts * 1000);
  if (Number.isNaN(then.getTime())) return "Date unknown";
  const diff = Date.now() - then.getTime();
  const day = 86400000;
  if (diff < 0) return "Just posted";
  if (diff < 3600000) return `${Math.max(1, Math.round(diff / 60000))}m ago`;
  if (diff < day) return `${Math.round(diff / 3600000)}h ago`;
  if (diff < 14 * day) return `${Math.round(diff / day)}d ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function showBanner(text, isError) {
  const banner = document.getElementById("banner");
  if (!text) {
    banner.hidden = true;
    banner.textContent = "";
    banner.classList.remove("error");
    return;
  }
  banner.hidden = false;
  banner.textContent = text;
  banner.classList.toggle("error", Boolean(isError));
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || data.refresh?.message || "The request failed.");
  }
  return data;
}

function splitComma(id) {
  return document
    .getElementById(id)
    .value.split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function splitLoose(id) {
  return document
    .getElementById(id)
    .value.split(/[\n,]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function profileFromForm() {
  return {
    name: document.getElementById("name").value.trim(),
    email: document.getElementById("email").value.trim(),
    phone: document.getElementById("phone").value.trim(),
    contact: document.getElementById("contact").value.trim(),
    links: splitLoose("links"),
    target_titles: splitComma("titles"),
    skills: splitComma("skills"),
    location: document.getElementById("location").value.trim(),
    keywords: splitComma("keywords"),
    summary: document.getElementById("summary").value.trim(),
    experience: document.getElementById("experience").value.trim(),
    education: document.getElementById("education").value.trim(),
    weekly_target: Number(document.getElementById("weekly-target").value || 0),
    filters: filtersFromForm(),
  };
}

function originalUrl(job) {
  const candidates = [job && job.original_url, job && job.url, job && job.apply_url];
  for (const value of candidates) {
    if (typeof value === "string" && /^https?:\/\//i.test(value.trim())) return value.trim();
  }
  return "";
}

function sourceLabel(job) {
  const draft = (job && job.draft) || {};
  const source = draft.source || job.match_source;
  const model = draft.model || job.match_model;
  if (source === "ollama" && model) {
    return (
      "Notes from " +
      model +
      " via Ollama on this Mac's GPU, checked against your profile. The score is the built-in overlap count."
    );
  }
  return "Notes and score from the built-in matcher on this computer.";
}

function filtersFromForm() {
  return {
    keyword: document.getElementById("filter-keyword").value.trim(),
    role: document.getElementById("filter-role").value.trim(),
    location: document.getElementById("filter-location").value.trim(),
    status: statusFilter,
    min_score: Number(document.getElementById("filter-score").value || 0),
  };
}

function fillProfile(profile) {
  document.getElementById("name").value = profile.name || "";
  document.getElementById("email").value = profile.email || "";
  document.getElementById("phone").value = profile.phone || "";
  document.getElementById("contact").value = profile.contact || "";
  document.getElementById("links").value = (profile.links || []).join("\n");
  document.getElementById("titles").value = (profile.target_titles || []).join(", ");
  document.getElementById("skills").value = (profile.skills || []).join(", ");
  document.getElementById("location").value = profile.location || "";
  document.getElementById("keywords").value = (profile.keywords || []).join(", ");
  document.getElementById("summary").value = profile.summary || "";
  document.getElementById("experience").value = profile.experience || "";
  document.getElementById("education").value = profile.education || "";
  const target = document.getElementById("weekly-target");
  if (target && document.activeElement !== target) target.value = String(profile.weekly_target ?? 5);
  const filters = profile.filters || {};
  document.getElementById("filter-keyword").value = filters.keyword || "";
  document.getElementById("filter-role").value = filters.role || "";
  document.getElementById("filter-location").value = filters.location || "";
  document.getElementById("filter-score").value = String(filters.min_score || 0);
  statusFilter = filters.status || "new";
}

function localWarnings(profile) {
  const warnings = [];
  if (!profile.name || profile.name.toLowerCase() === "your name") {
    warnings.push("The profile name is still “Your Name”. Change it before you send a note.");
  }
  if ((profile.summary || "").startsWith("Replace this with")) {
    warnings.push("The summary is still the example text. Rewrite it before you send a note.");
  }
  if (!profile.skills.length) {
    warnings.push("Add skills so drafts can point at real overlap with each posting.");
  }
  return warnings;
}

function renderWarnings() {
  const list = document.getElementById("profile-warnings");
  const warnings = booted ? localWarnings(profileFromForm()) : [];
  list.replaceChildren(...warnings.map((warning) => h("li", {}, warning)));
}

function applyState(data, refillProfile) {
  jobs = data.jobs || [];
  lastNewIds = new Set(data.meta?.last_new_ids || []);
  const when = data.meta?.last_refresh_at
    ? `Last refresh ${new Date(data.meta.last_refresh_at).toLocaleString()}`
    : "No refresh yet";
  const total = data.meta?.total ?? jobs.length;
  document.getElementById("refresh-meta").textContent = `${when} · ${total} stored`;
  if (refillProfile && data.profile) fillProfile(data.profile);
  renderDesk(data.meta);
  renderWarnings();
  renderTabs();
  renderQueue();
  renderList();
  refreshDetailMeta();
}

function renderDesk(meta) {
  const ai = (meta && meta.ai) || {};
  const note = document.getElementById("ai-note");
  if (note) note.textContent = ai.label || "Built-in matcher";
  if (meta && meta.week) weekApplied = Number(meta.week.applied || 0);
  renderWeek();
}

function renderWeek() {
  const note = document.getElementById("week-note");
  const target = document.getElementById("weekly-target");
  if (!note) return;
  const value = target ? target.value || "0" : "0";
  note.textContent = `This week: ${weekApplied} in Applied, Interviewing, or Offer · target ${value}. This reminder does not block the queue.`;
}

function renderQueue() {
  const root = document.getElementById("queue-list");
  const note = document.getElementById("queue-note");
  if (!root) return;
  const waiting = jobs.filter((job) => job.agent && job.agent.mode === "supervised" && job.status === "ready");
  if (note) {
    note.textContent = waiting.length
      ? `${waiting.length} waiting for you. Supervised mode stops here. Nothing is submitted.`
      : "Supervised mode only works on jobs you add. It stops at Ready so you can review. Nothing is submitted.";
  }
  root.replaceChildren();
  if (!waiting.length) {
    root.append(h("p", { class: "fine" }, "The queue is empty."));
    return;
  }
  for (const job of waiting.slice(0, 8)) {
    root.append(
      h(
        "button",
        { type: "button", class: "queue-item", onclick: () => selectJob(job.id) },
        `${job.title} · ${job.company}`
      )
    );
  }
}

async function reloadWeek() {
  try {
    const response = await fetch("/api/meta");
    const data = await readJson(response);
    renderDesk(data);
  } catch (error) {
    /* The week line updates on the next full load. */
  }
}

function renderTabs() {
  const counts = { all: jobs.length };
  for (const status of STATUS_ORDER) {
    if (status !== "all") counts[status] = 0;
  }
  for (const job of jobs) counts[job.status] = (counts[job.status] || 0) + 1;
  for (const status of STATUS_ORDER) {
    const button = document.querySelector(`[data-status="${status}"]`);
    const count = button.querySelector("[data-count]");
    count.textContent = String(counts[status] || 0);
    button.setAttribute("aria-pressed", String(statusFilter === status));
  }
}

function matches(job, filters) {
  if (filters.status && filters.status !== "all" && job.status !== filters.status) return false;
  if ((job.score || 0) < (Number(filters.min_score) || 0)) return false;
  const blob =
    job.search_text ||
    `${job.title} ${job.company} ${job.location} ${(job.tags || []).join(" ")}`.toLowerCase();
  if (filters.keyword && !blob.includes(filters.keyword.toLowerCase())) return false;
  if (filters.role && !(job.title || "").toLowerCase().includes(filters.role.toLowerCase())) return false;
  if (filters.location) {
    const want = filters.location.toLowerCase();
    const loc = (job.location || "").toLowerCase();
    const remoteWant = want === "remote" || want === "anywhere";
    const remoteJob =
      job.remote || loc.includes("remote") || loc.includes("worldwide") || loc.includes("anywhere");
    if (remoteWant) {
      if (!remoteJob && !loc.includes(want) && !blob.includes(want)) return false;
    } else if (!loc.includes(want) && !blob.includes(want)) return false;
  }
  return true;
}

function renderList() {
  const root = document.getElementById("list");
  const filters = filtersFromForm();
  const matched = jobs
    .filter((job) => matches(job, filters))
    .slice()
    .sort((a, b) => (b.published_ts || 0) - (a.published_ts || 0) || (b.score || 0) - (a.score || 0));
  document.getElementById("list-count").textContent = jobs.length
    ? `${matched.length} shown · newest first`
    : "";
  root.replaceChildren();
  if (!matched.length) {
    let message = "Nothing in this view. Try All, or lower the minimum match.";
    if (!jobs.length && loadingJobs) message = "Looking for new public postings…";
    else if (!jobs.length) message = "No posts stored yet. Use Refresh new jobs.";
    root.append(h("p", { class: "empty" }, message));
    return;
  }
  for (const job of matched.slice(0, listLimit)) {
    const titleKids = [job.title];
    const showJustIn = lastNewIds.size > 0 && lastNewIds.size < jobs.length;
    if (showJustIn && lastNewIds.has(job.id)) titleKids.push(h("span", { class: "badge" }, "Just in"));
    const url = originalUrl(job);
    const openPosting = () => {
      if (!url) {
        showBanner("The original link is missing for this posting.", true);
        selectJob(job.id);
        return;
      }
      window.open(url, "_blank", "noopener,noreferrer");
      recordOpen(job.id);
      selectJob(job.id);
    };
    const title = url
      ? h(
          "a",
          {
            class: "job-title",
            href: url,
            target: "_blank",
            rel: "noopener noreferrer",
            onclick: (event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
              event.preventDefault();
              window.open(url, "_blank", "noopener,noreferrer");
              recordOpen(job.id);
              selectJob(job.id);
            },
          },
          titleKids
        )
      : h(
          "button",
          {
            class: "job-title missing",
            type: "button",
            onclick: (event) => {
              event.stopPropagation();
              showBanner("The original link is missing for this posting.", true);
              selectJob(job.id);
            },
          },
          titleKids
        );
    root.append(
      h(
        "article",
        {
          class: "job" + (job.id === selectedId ? " selected" : ""),
          onclick: (event) => {
            if (event.target.closest("a, button")) return;
            openPosting();
          },
        },
        [
          h("div", { class: "job-top" }, [
            title,
            h("span", { class: "score " + band(job.score) }, String(job.score || 0)),
          ]),
          h("p", { class: "company" }, job.company),
          h("div", { class: "job-meta" }, [
            h("span", {}, [job.location || "Location unknown", " · ", formatWhen(job.published_ts)]),
            h("span", { class: "pill " + (job.status || "new") }, job.status || "new"),
          ]),
          h("p", { class: "reason" }, (job.match_reasons && job.match_reasons[0]) || ""),
          job.gaps_ready && job.gaps && job.gaps.length
            ? h("p", { class: "gaps-line" }, "Gaps: " + job.gaps.slice(0, 3).join(", "))
            : null,
          h(
            "button",
            {
              class: "btn ghost tiny",
              type: "button",
              onclick: (event) => {
                event.stopPropagation();
                selectJob(job.id);
              },
            },
            "Review match"
          ),
        ]
      )
    );
  }
  if (matched.length > listLimit) {
    root.append(
      h(
        "button",
        {
          class: "btn ghost",
          type: "button",
          onclick: () => {
            listLimit += 40;
            renderList();
          },
        },
        `Show more (${matched.length - listLimit} left)`
      )
    );
  }
}

function mergeJob(job) {
  const index = jobs.findIndex((item) => item.id === job.id);
  if (index === -1) {
    jobs.push(job);
    return;
  }
  const previous = jobs[index];
  const next = Object.assign({}, previous, job);
  if (job.draft == null && previous.draft) next.draft = previous.draft;
  if (!job.description && previous.description) next.description = previous.description;
  jobs[index] = next;
}

function refreshDetailMeta() {
  if (!detail) return;
  const updated = jobs.find((job) => job.id === detail.id);
  if (!updated) return;
  detail.score = updated.score;
  detail.match_reasons = updated.match_reasons;
  const reasons = document.getElementById("live-reasons");
  if (reasons) reasons.replaceChildren(...(updated.match_reasons || []).map((reason) => h("li", {}, reason)));
  const score = document.getElementById("live-score");
  if (score) {
    score.textContent = String(updated.score || 0);
    score.className = "score " + band(updated.score || 0);
  }
}

function gapBlock(job) {
  if (!job.gaps_ready && job.gap_note !== "missing" && job.gap_note !== "none_missing" && job.gap_note !== "none_detected") {
    return h("p", { class: "fine" }, "Gaps show up with the draft.");
  }
  if (job.gap_note === "none_detected") {
    return h(
      "p",
      { class: "fine" },
      "No clear required-skill list was found in this posting, so no gaps were marked. Skills that are only on your profile are not gaps."
    );
  }
  if (!job.gaps || !job.gaps.length) {
    return h("p", { class: "fine" }, "No skill this posting emphasizes is missing from your profile.");
  }
  return h("div", {}, [
    h("h3", {}, "Gaps"),
    h(
      "p",
      { class: "fine" },
      "Skills this posting emphasizes that are not on your profile. Skills you have that this job does not ask for are not listed."
    ),
    h("ul", { class: "gaps" }, job.gaps.map((gap) => h("li", {}, gap))),
  ]);
}

function markDraftDirty() {
  draftDirty = true;
}

function renderDetail() {
  const root = document.getElementById("detail");
  root.replaceChildren();
  draftDirty = false;
  if (!detail) {
    root.append(
      h("div", { class: "empty" }, [
        h("h2", {}, "Pick a job"),
        h(
          "p",
          {},
          "Click a title to open the original posting in a new tab. The match, gaps, and draft stay on this page. You submit the application yourself."
        ),
      ])
    );
    return;
  }
  const job = detail;
  const draft = job.draft || {};
  const url = originalUrl(job);
  const bullets = draft.bullet_emphasis || [];
  const bulletFields = (bullets.length ? bullets : [""]).map((line) =>
    h("textarea", { class: "bullet", rows: "3", oninput: markDraftDirty }, line || "")
  );
  const tracker = h(
    "select",
    {
      id: "tracker-status",
      onchange: (event) => {
        const next = event.target.value;
        if (next && next !== job.status) setStatus(next);
      },
    },
    TRACKER.map(([value, label]) => h("option", { value, selected: value === job.status }, label))
  );
  const postingLink = url
    ? h(
        "a",
        {
          class: "btn primary apply",
          href: url,
          target: "_blank",
          rel: "noopener noreferrer",
          onclick: () => recordOpen(job.id),
        },
        "Open original posting"
      )
    : h("p", { class: "missing-link" }, "The original link is missing for this posting.");
  const nodes = [
    h("p", { class: "source-line" }, [
      "Listing: ",
      url
        ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, job.source || "Original posting")
        : job.source || "Source feed",
      job.also_seen_on && job.also_seen_on.length ? ` · Also seen on ${job.also_seen_on.join(", ")}` : "",
    ]),
    h("h2", {}, job.title),
    h("p", { class: "company" }, job.company),
    h("div", { class: "detail-meta" }, [
      h("span", {}, [
        job.location || "Location unknown",
        " · ",
        formatWhen(job.published_ts),
        job.salary ? ` · ${job.salary}` : "",
      ]),
      h("span", {}, [
        h("span", { id: "live-score", class: "score " + band(job.score || 0) }, String(job.score || 0)),
        " match · ",
        h("span", { class: "pill " + job.status, id: "status-pill" }, job.status),
      ]),
    ]),
    h("p", { class: "fine source-note" }, sourceLabel(job)),
    job.tags && job.tags.length ? h("ul", { class: "tags" }, job.tags.slice(0, 8).map((tag) => h("li", {}, tag))) : null,
    h("h3", {}, "Why it fits"),
    h("ul", { class: "reasons", id: "live-reasons" }, (job.match_reasons || []).map((reason) => h("li", {}, reason))),
    gapBlock(job),
    postingLink,
    h("p", { class: "fine" }, "The original posting opens in a new tab. This page does not submit the application."),
    job.link_opened_at ? h("p", { class: "fine", id: "opened-note" }, "You have opened this link from here.") : null,
    job.agent
      ? h("p", { class: "queue-flag" }, job.agent.note || "In the supervised queue. Nothing was submitted.")
      : null,
    h("div", { class: "actions" }, [
      h("button", { class: "btn gold", type: "button", id: "add-btn", onclick: addToQueue }, "Add to supervised queue"),
      h("button", { class: "btn", type: "button", id: "draft-btn", onclick: generateDraft }, draft.cover_note || draft.resume_summary ? "Draft again" : "Draft materials"),
    ]),
    h("label", { class: "tracker-label" }, ["Tracker", tracker]),
    h("div", { class: "draft-box", id: "draft-box" }, [
      h("h3", {}, "Tailored for this job"),
      (draft.warnings || []).length
        ? h("ul", { class: "warnings" }, draft.warnings.map((warning) => h("li", {}, warning)))
        : null,
      h("label", {}, [
        "Resume summary",
        h("textarea", { id: "resume-summary", rows: "5", oninput: markDraftDirty }, draft.resume_summary || ""),
      ]),
      h("p", { class: "fine" }, "Bullet emphasis from your experience"),
      ...bulletFields,
      h("label", {}, [
        "Cover note",
        h("textarea", { id: "cover", rows: "10", oninput: markDraftDirty }, draft.cover_note || ""),
      ]),
      h("div", { class: "actions" }, [
        h("button", { class: "btn primary", type: "button", onclick: () => saveDraft() }, "Save draft"),
        h("button", { class: "btn ghost", type: "button", onclick: copyDraft }, "Copy draft"),
      ]),
      h("p", { class: "fine", id: "draft-state" }, "Kept on this computer. Paste it into the employer form yourself."),
    ]),
    h("h3", {}, "Posting text"),
    h("div", { class: "description" }, job.description || "This feed did not include a description."),
  ];
  root.append(...nodes.filter(Boolean));
}

let selectToken = 0;

async function selectJob(id) {
  const token = ++selectToken;
  selectedId = id;
  renderList();
  const root = document.getElementById("detail");
  root.replaceChildren(h("p", { class: "empty" }, "Preparing the match score and draft on this computer…"));
  try {
    const response = await fetch("/api/jobs/" + encodeURIComponent(id));
    const job = await readJson(response);
    if (token !== selectToken) return;
    detail = job;
    mergeJob(job);
    renderTabs();
    renderQueue();
    renderList();
    renderDetail();
  } catch (error) {
    if (token !== selectToken) return;
    showBanner(error.message, true);
  }
}

function collectDraft() {
  const cover = document.getElementById("cover");
  const summary = document.getElementById("resume-summary");
  if (!cover && !summary) return null;
  const answers = [];
  document.querySelectorAll("textarea.answer").forEach((field) => {
    answers.push({ question: field.getAttribute("data-question") || "Answer", answer: field.value });
  });
  const bullet_emphasis = [];
  document.querySelectorAll("textarea.bullet").forEach((field) => {
    if (field.value.trim()) bullet_emphasis.push(field.value.trim());
  });
  return {
    cover_note: cover ? cover.value : "",
    resume_summary: summary ? summary.value : "",
    bullet_emphasis,
    answers,
  };
}

async function saveDraft(silent) {
  if (!detail) return;
  const body = collectDraft();
  if (!body) return;
  const response = await fetch("/api/jobs/" + encodeURIComponent(detail.id) + "/draft", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const job = await readJson(response);
  detail = job;
  mergeJob(job);
  const pill = document.getElementById("status-pill");
  if (pill) {
    pill.textContent = job.status;
    pill.className = "pill " + job.status;
  }
  const state = document.getElementById("draft-state");
  if (state && !silent) state.textContent = "Draft saved on this computer.";
  draftDirty = false;
  renderTabs();
  renderQueue();
  renderList();
}

async function addToQueue() {
  if (!detail) return;
  const button = document.getElementById("add-btn");
  if (button) {
    button.disabled = true;
    button.textContent = "Preparing…";
  }
  try {
    if (draftDirty) await saveDraft(true);
    const response = await fetch("/api/jobs/" + encodeURIComponent(detail.id) + "/add", { method: "POST" });
    const job = await readJson(response);
    detail = job;
    mergeJob(job);
    draftDirty = false;
    renderTabs();
    renderQueue();
    renderList();
    renderDetail();
    const stopped = job.agent && job.agent.stopped_at;
    showBanner(
      stopped === "ready"
        ? "In the supervised queue as ready. Nothing was submitted."
        : (job.agent && job.agent.note) || "Updated.",
      false
    );
    reloadWeek();
  } catch (error) {
    showBanner(error.message, true);
    if (button) {
      button.disabled = false;
      button.textContent = "Add to supervised queue";
    }
  }
}

async function generateDraft() {
  if (!detail) return;
  if (detail.draft && !confirm("Replace the current draft with a new one from your profile?")) return;
  const button = document.getElementById("draft-btn");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/jobs/" + encodeURIComponent(detail.id) + "/draft", { method: "POST" });
    const job = await readJson(response);
    detail = job;
    mergeJob(job);
    renderTabs();
    renderList();
    renderDetail();
  } catch (error) {
    showBanner(error.message, true);
    if (button) button.disabled = false;
  }
}

async function setStatus(status) {
  if (!detail) return;
  if (status === "applied") {
    const confirmed = confirm(
      "Mark this as applied only after you submit on the employer site. This app does not submit it for you."
    );
    if (!confirmed) return;
  }
  try {
    if (document.getElementById("cover")) await saveDraft(true);
    const response = await fetch("/api/jobs/" + encodeURIComponent(detail.id) + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const job = await readJson(response);
    detail = job;
    mergeJob(job);
    renderTabs();
    renderQueue();
    renderList();
    renderDetail();
    reloadWeek();
  } catch (error) {
    showBanner(error.message, true);
  }
}

function recordOpen(id) {
  fetch("/api/jobs/" + encodeURIComponent(id) + "/opened", { method: "POST" })
    .then((response) => readJson(response))
    .then((job) => {
      if (detail && detail.id === job.id) {
        detail = job;
        mergeJob(job);
        if (!document.getElementById("opened-note")) {
          const anchor = document.querySelector(".apply");
          if (anchor) {
            anchor.insertAdjacentElement(
              "afterend",
              h("p", { class: "fine", id: "opened-note" }, "You have opened this link from here.")
            );
          }
        }
      }
    })
    .catch(() => {});
}

async function copyDraft() {
  const cover = document.getElementById("cover");
  if (!cover) return;
  const chunks = [];
  const summary = document.getElementById("resume-summary");
  if (summary && summary.value.trim()) chunks.push("Resume summary\n" + summary.value.trim());
  const bullets = [];
  document.querySelectorAll("textarea.bullet").forEach((field) => {
    if (field.value.trim()) bullets.push("- " + field.value.trim());
  });
  if (bullets.length) chunks.push("Bullet emphasis\n" + bullets.join("\n"));
  chunks.push(cover.value.trim());
  document.querySelectorAll("textarea.answer").forEach((field) => {
    const question = field.getAttribute("data-question") || "Answer";
    chunks.push(question + "\n" + field.value.trim());
  });
  const text = chunks.filter(Boolean).join("\n\n");
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    cover.focus();
    cover.select();
    document.execCommand("copy");
  }
  const state = document.getElementById("draft-state");
  if (state) state.textContent = "Copied. Paste it into the employer form.";
}

function scheduleSave() {
  if (!booted) return;
  const gen = ++saveGen;
  const label = document.getElementById("save-state");
  label.textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => persist(gen), 700);
  renderWarnings();
  renderList();
}

async function persist(gen) {
  const version = stateVersion;
  try {
    const response = await fetch("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profileFromForm()),
    });
    const data = await readJson(response);
    if (gen !== saveGen || version !== stateVersion) return;
    jobs = data.jobs || jobs;
    document.getElementById("save-state").textContent = "Saved";
    renderTabs();
    renderList();
    refreshDetailMeta();
    renderWarnings();
  } catch (error) {
    document.getElementById("save-state").textContent = error.message;
  }
}

async function refreshJobs() {
  stateVersion += 1;
  const button = document.getElementById("refresh");
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "Refreshing…";
  loadingJobs = true;
  renderList();
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    const data = await response.json();
    if (data.jobs) applyState(data, false);
    const report = data.refresh || {};
    showBanner(report.message || data.error || "Refresh finished.", !response.ok);
    if (!response.ok && !data.jobs) throw new Error(data.error || "Refresh failed.");
  } catch (error) {
    showBanner(error.message, true);
  } finally {
    loadingJobs = false;
    button.disabled = false;
    button.textContent = previous;
    renderList();
  }
}

function bind() {
  document.getElementById("profile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const gen = ++saveGen;
    persist(gen);
  });
  ["name", "email", "phone", "contact", "links", "titles", "skills", "location", "keywords", "summary", "experience", "education"].forEach((id) => {
    document.getElementById(id).addEventListener("input", scheduleSave);
  });
  document.getElementById("extract-resume").addEventListener("click", () => {
    const input = document.getElementById("resume-file");
    if (!input.files || !input.files[0]) {
      const status = document.getElementById("extract-status");
      status.textContent = "Choose a PDF, DOCX, TXT, or MD file first.";
      status.classList.add("error-text");
      return;
    }
    uploadResume(input.files[0]);
  });
  document.getElementById("resume-file").addEventListener("change", () => {
    const input = document.getElementById("resume-file");
    if (input.files && input.files[0]) uploadResume(input.files[0]);
  });
  const weekly = document.getElementById("weekly-target");
  if (weekly) {
    weekly.addEventListener("input", () => {
      renderWeek();
      scheduleSave();
    });
  }
  ["filter-keyword", "filter-role", "filter-location", "filter-score"].forEach((id) => {
    document.getElementById(id).addEventListener("input", () => {
      listLimit = 40;
      scheduleSave();
    });
  });
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      statusFilter = button.getAttribute("data-status");
      listLimit = 40;
      renderTabs();
      scheduleSave();
    });
  });
  document.getElementById("refresh").addEventListener("click", refreshJobs);
}

async function boot() {
  bind();
  renderDetail();
  try {
    const response = await fetch("/api/state");
    const data = await readJson(response);
    applyState(data, true);
    booted = true;
    document.getElementById("extract-resume").disabled = false;
    renderWarnings();
    if (!jobs.length) refreshJobs();
  } catch (error) {
    booted = true;
    document.getElementById("extract-resume").disabled = false;
    showBanner(error.message, true);
  }
}

function showExtraction(result) {
  const box = document.getElementById("extract-preview");
  const status = document.getElementById("extract-status");
  box.hidden = false;
  box.classList.toggle("thin", Boolean(result.thin));
  status.classList.remove("error-text");
  status.textContent = result.message || "";
  const rows = result.field_preview || [];
  const list = document.getElementById("extract-fields");
  if (!rows.length) {
    list.replaceChildren(h("li", {}, result.thin ? "No fields filled." : "No profile fields were detected."));
  } else {
    list.replaceChildren(...rows.map((row) => h("li", {}, row)));
  }
  const chars = Number(result.chars || 0);
  document.getElementById("extract-raw-label").textContent =
    "Text that was read (" + chars.toLocaleString() + " characters)";
  document.getElementById("extract-sample").textContent = result.preview || "(No text found.)";
  document.getElementById("extract-raw").open = Boolean(result.thin);
}

function applyExtracted(result) {
  showExtraction(result);
  if (result.thin) return;
  const profile = profileFromForm();
  const fields = result.fields || {};
  for (const key of result.filled || []) {
    const value = fields[key];
    if (value == null) continue;
    if (typeof value === "string" && !value.trim()) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    profile[key] = value;
  }
  fillProfile(profile);
  clearTimeout(saveTimer);
  const gen = ++saveGen;
  document.getElementById("save-state").textContent = "Saving…";
  return persist(gen);
}

async function uploadResume(file) {
  const button = document.getElementById("extract-resume");
  const status = document.getElementById("extract-status");
  const lower = (file.name || "").toLowerCase();
  if (![".pdf", ".docx", ".txt", ".md"].some((ext) => lower.endsWith(ext))) {
    status.textContent = "Upload a PDF, DOCX, or plain text file (.txt or .md).";
    status.classList.add("error-text");
    document.getElementById("extract-preview").hidden = true;
    return;
  }
  button.disabled = true;
  status.classList.remove("error-text");
  status.textContent = "Reading the resume on this computer…";
  try {
    const body = new FormData();
    body.append("resume", file, file.name);
    const response = await fetch("/api/profile/resume", { method: "POST", body });
    const result = await readJson(response);
    await applyExtracted(result);
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error-text");
    document.getElementById("extract-preview").hidden = true;
  } finally {
    button.disabled = false;
  }
}

boot();
