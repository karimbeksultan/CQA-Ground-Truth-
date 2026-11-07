// CQA workbench: dialogs, evaluation card, appeals, checklists. Plain ES module, no build step.
// API base: <html data-api="https://host"> or window.CQA_API_BASE, default same origin (embeddable in D365).
import { t, setLang, getLang, LANGS, LANG_LABEL } from "./i18n.js";

const API = (document.documentElement.dataset.api || window.CQA_API_BASE || "").replace(/\/$/, "");
const $main = document.getElementById("main");
const $rail = document.getElementById("rail");

// ---------- small helpers ----------
const store = {
  get(k, d) { try { return localStorage.getItem("cqa." + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cqa." + k, v); } catch { /* storage unavailable: keep in memory */ } },
};

const ICONS = {
  dialogs: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v10H9l-5 4z"/></svg>',
  appeals: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0z"/></svg>',
  checklists: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v10H9l-5 4z"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5v14l11-7z"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20zM12 9v5M12 17h.01"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>',
  run: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4v16l13-8z"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
};

/** DOM builder. Strings become text nodes (never HTML), so transcript text cannot inject markup. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "icon") el.insertAdjacentHTML("afterbegin", ICONS[v]); // static, authored SVG only
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.setAttribute("style", v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      ...opts,
      headers: opts.body ? { "Content-Type": "application/json", ...(opts.headers || {}) } : opts.headers,
    });
  } catch {
    throw new Error(t("err.network"));
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error?.message || body?.detail?.[0]?.msg || `HTTP ${res.status}`);
  return body;
}

function toast(msg, kind = "") {
  const el = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : false }, msg);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 4000);
}

const fmt = (n) => (n == null ? "—" : (Math.round(n * 100) / 100).toLocaleString(getLang() === "en" ? "en-US" : "ru-RU"));
const scoreClass = (n) => (n == null ? "" : n >= 85 ? "hi" : n >= 60 ? "mid" : "lo");
const navigate = (hash) => { if (location.hash !== hash) location.hash = hash; else route(); };

function answerLabel(a, crit) {
  if (a === "yes") return t("ans.yes");
  if (a === "no") return t("ans.no");
  if (a === "na") return t("ans.na");
  if (crit?.type === "scale") return `${a} / ${Math.max(...crit.scale.map((s) => s.value))}`;
  return String(a);
}
function answerClass(a, crit) {
  if (a === "yes") return crit?.type === "flag" ? "no" : "yes";
  if (a === "no") return crit?.type === "flag" ? "yes" : "no";
  if (a === "na") return "na";
  const max = Math.max(...(crit?.scale || [{ value: 1 }]).map((s) => s.value));
  return a >= max ? "yes" : a <= 0 ? "no" : "part";
}
function allowedAnswers(crit) {
  const base = crit.type === "scale" ? crit.scale.map((s) => s.value).sort((a, b) => b - a) : ["yes", "no"];
  return crit.na_allowed ? [...base, "na"] : base;
}

// ---------- state ----------
const state = {
  role: store.get("role", "analyst"),
  conversations: [],
  checklists: [],
  jobs: [],
  engine: "",
  openAppeals: 0,
  selected: new Set(),
  filters: { q: "", channel: "", language: "", group: store.get("group", ""), crit: "" },
  engineOptions: [],
  sort: { key: "conversation_id", dir: 1 },
  checklistChoice: "",
  evalCache: new Map(),
};
setLang(store.get("lang", "ru"));

// ---------- shell ----------
function renderRail() {
  const current = location.hash.split("/")[1] || "";
  const link = (key, href, icon, extra) => h("a", { href, icon, "aria-current": current === key ? "page" : false },
    h("span", { class: "label" }, t(`nav.${key === "" ? "dialogs" : key}`)), extra);
  const seg = (items, value, onPick, labelId) => h("div", { class: "seg", role: "group", "aria-labelledby": labelId },
    items.map(([v, label]) => h("button", { type: "button", "aria-pressed": String(v === value), onclick: () => onPick(v) }, label)));
  $rail.replaceChildren(
    h("div", { class: "brand" }, h("div", { class: "brand-mark", "aria-hidden": "true" }, "F"),
      h("div", { class: "brand-name" }, "CQA · Freedom", h("small", {}, t("brand.sub")))),
    h("nav", { class: "nav", "aria-label": "CQA" },
      link("", "#/", "dialogs"),
      link("appeals", "#/appeals", "appeals", state.openAppeals ? h("span", { class: "count" }, state.openAppeals) : null),
      link("checklists", "#/checklists", "checklists")),
    h("div", { class: "rail-foot" },
      h("div", {}, h("label", { id: "lbl-role" }, t("role.label")),
        seg([["analyst", t("role.analyst")], ["reviewer", t("role.reviewer")]], state.role,
          (v) => { state.role = v; store.set("role", v); renderRail(); route(); }, "lbl-role")),
      h("div", {}, h("label", { id: "lbl-lang" }, t("lang.label")),
        seg(LANGS.map((l) => [l, LANG_LABEL[l]]), getLang(),
          (v) => { setLang(v); store.set("lang", v); renderRail(); route(); }, "lbl-lang")),
      h("button", { type: "button", class: "btn ghost sm big-toggle", "aria-pressed": String(document.documentElement.classList.contains("big")),
        onclick: () => { const on = document.documentElement.classList.toggle("big"); store.set("big", on ? "1" : ""); renderRail(); } }, t("view.big")),
      state.engine ? h("div", { class: "engine-note" }, t("engine.label", { m: state.engine })) : null),
  );
}

// ---------- jobs ----------
let pollTimer = null;
async function refreshJobs() {
  try {
    const jobs = await api("/api/jobs");
    const wasRunning = new Set(state.jobs.filter((j) => j.status !== "finished" && j.status !== "failed").map((j) => j.id));
    state.jobs = jobs.slice(-6);
    const finished = jobs.filter((j) => wasRunning.has(j.id) && (j.status === "finished" || j.status === "failed"));
    const running = jobs.some((j) => j.status === "queued" || j.status === "running");
    if (finished.length || running) {
      await refreshConversations();
      renderRail();
      onJobsChanged(finished);
    }
    clearTimeout(pollTimer);
    if (running) pollTimer = setTimeout(refreshJobs, 2500);
  } catch { clearTimeout(pollTimer); pollTimer = setTimeout(refreshJobs, 5000); }
}
let onJobsChanged = () => {};

function jobStrip() {
  const live = (j) => j.status === "queued" || j.status === "running";
  const recent = state.jobs.filter((j) => live(j) || Date.now() - Date.parse(j.finished_at || j.updated_at || 0) < 10 * 60e3).slice(-3);
  if (!recent.length) return null;
  return h("div", { class: "jobs" }, recent.map((j) => {
    const live = j.status === "queued" || j.status === "running";
    const pct = j.total ? Math.round(((j.done + j.failed) / j.total) * 100) : 0;
    const label = j.status === "queued" ? t("job.queued", { total: j.total })
      : live ? t("job.running", { done: j.done + j.failed, total: j.total })
      : t("job.finished", { done: j.done, total: j.total });
    return h("div", { class: `job ${live ? "" : "done"}` },
      h("span", { class: "pulse", "aria-hidden": "true" }),
      h("div", {}, h("div", { style: "display:flex;gap:12px;align-items:baseline;margin-bottom:6px" },
        h("b", {}, label), j.failed ? h("span", { class: "state failed" }, t("job.failedN", { n: j.failed })) : null,
        live ? h("span", { class: "muted", style: "font-size:.82rem" }, t("job.hint")) : null),
        h("div", { class: "bar", role: "progressbar", "aria-label": label, "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
          h("i", { class: j.failed && !j.done ? "fail" : "", style: `width:${pct}%` }))),
      h("span", { class: "meta" }, `${pct}%`));
  }));
}

async function startJob(ids, checklistId, engine) {
  const body = { conversation_ids: ids, ...(checklistId ? { checklist_id: checklistId } : {}), ...(engine ? { engines: [engine] } : {}) };
  const job = await api("/api/evaluations", { method: "POST", body: JSON.stringify(body) });
  state.jobs.push(job);
  toast(t("toast.jobStarted", { n: ids.length }), "ok");
  refreshJobs();
  return job;
}

async function refreshConversations() {
  state.conversations = await api("/api/conversations");
  state.openAppeals = state.conversations.reduce((s, c) => s + (c.latest_evaluation?.open_appeals || 0), 0);
}

// ---------- dialogs view ----------
// The toolbar is built once per visit; the job strip and the table refresh in place, so typing,
// focus and selection survive the 2.5 s job polls.
let dialogsRefs = null;

function scopeRows() {
  const f = state.filters;
  return state.conversations
    .filter((c) => !f.group || c.group_id === f.group)
    .filter((c) => !f.channel || c.channel === f.channel)
    .filter((c) => !f.language || c.language === f.language);
}

const lostPoints = (c, id) => {
  const v = c.latest_evaluation?.status === "done" ? c.latest_evaluation.criteria?.[id] : undefined;
  return v != null && v < 1;
};

function filteredRows() {
  const f = state.filters;
  return scopeRows()
    .filter((c) => !f.q || c.conversation_id.toLowerCase().includes(f.q.toLowerCase()))
    .filter((c) => !f.crit || lostPoints(c, f.crit))
    .sort((a, b) => {
      const key = state.sort.key;
      const va = key === "total" ? a.latest_evaluation?.total ?? -1 : a[key];
      const vb = key === "total" ? b.latest_evaluation?.total ?? -1 : b[key];
      return (va > vb ? 1 : va < vb ? -1 : 0) * state.sort.dir;
    });
}

function selectedIds() {
  const f = state.filters;
  return [...state.selected].filter((id) => state.conversations.some((c) => c.conversation_id === id && (!f.group || c.group_id === f.group)));
}
function selectedCount() { return selectedIds().length; }

function updateEvalButton() {
  if (!dialogsRefs) return;
  const n = selectedCount();
  dialogsRefs.evalLabel.textContent = t("dialogs.evaluate", { n });
  dialogsRefs.evalBtn.disabled = !n || state.role !== "analyst";
}

function dialogsTable() {
  const rows = filteredRows();
  if (!rows.length) {
    return h("div", { class: "table-wrap" }, h("div", { class: "empty" },
      state.conversations.length ? h("p", {}, t("empty.filter"))
        : [h("h3", {}, t("empty.dialogs.title")), h("p", {}, t("empty.dialogs.text"))]));
  }
  const visibleIds = rows.map((r) => r.conversation_id);
  const rowChecks = [];
  const headCheck = h("input", { type: "checkbox", "aria-label": t("dialogs.selectAll"),
    checked: visibleIds.every((id) => state.selected.has(id)),
    onchange: (e) => {
      for (const box of rowChecks) {
        box.checked = e.target.checked;
        if (e.target.checked) state.selected.add(box.value); else state.selected.delete(box.value);
        box.closest("tr").classList.toggle("selected", e.target.checked);
      }
      updateEvalButton();
    } });
  const th = (key, label, cls = "") => {
    const sorted = state.sort.key === key;
    return h("th", { class: cls, scope: "col", "aria-sort": sorted ? (state.sort.dir > 0 ? "ascending" : "descending") : "none" },
      h("button", { type: "button", class: "th-sort", "data-key": key, onclick: () => {
        state.sort = { key, dir: sorted ? -state.sort.dir : 1 };
        refreshTable();
        dialogsRefs?.tableHost.querySelector(`.th-sort[data-key="${key}"]`)?.focus();
      } }, label, sorted ? h("span", { class: "arrow", "aria-hidden": "true" }, state.sort.dir > 0 ? "↑" : "↓") : null));
  };
  const open = (id) => navigate(`#/c/${encodeURIComponent(id)}`);
  return h("div", { class: "table-wrap" }, h("table", { class: "grid" },
    h("thead", {}, h("tr", {},
      h("th", { scope: "col", style: "width:36px" }, headCheck),
      th("conversation_id", t("col.id")), th("channel", t("col.channel")), th("language", t("col.lang")),
      th("turns", t("col.turns"), "num"), h("th", { scope: "col" }, t("col.status")), th("total", t("col.total"), "num"),
      h("th", { scope: "col" }, t("col.appeals")))),
    h("tbody", {}, rows.map((c) => {
      const ev = c.latest_evaluation;
      const running = state.jobs.some((j) => (j.status === "running" || j.status === "queued") && j.conversation_ids.includes(c.conversation_id));
      const box = h("input", { type: "checkbox", value: c.conversation_id, "aria-label": c.conversation_id, checked: state.selected.has(c.conversation_id),
        onchange: (e) => {
          if (e.target.checked) state.selected.add(c.conversation_id); else state.selected.delete(c.conversation_id);
          e.target.closest("tr").classList.toggle("selected", e.target.checked);
          headCheck.checked = rowChecks.every((b) => b.checked);
          updateEvalButton();
        } });
      rowChecks.push(box);
      return h("tr", { class: state.selected.has(c.conversation_id) ? "selected" : "", tabindex: "0", "aria-label": c.conversation_id,
        onclick: (e) => { if (!e.target.closest("input")) open(c.conversation_id); },
        onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) open(c.conversation_id); } },
        h("td", {}, box),
        h("td", { class: "id" }, c.conversation_id),
        h("td", {}, c.channel === "voice call"
          ? h("span", { class: "tag call", icon: "phone" }, t("dialogs.call"))
          : h("span", { class: "tag", icon: "chat" }, t("dialogs.chat"))),
        h("td", {}, h("span", { class: "tag lang" }, c.language)),
        h("td", { class: "num muted" }, c.turns),
        h("td", {}, running ? h("span", { class: "state" }, "…")
          : !ev ? h("span", { class: "state" }, t("status.none"))
          : ev.status === "failed" ? h("span", { class: "state failed" }, t("status.failed"))
          : h("span", { class: "state" }, t("status.done"),
            ev.gross_error ? h("span", { class: "flag-gross", icon: "warn", style: "margin-left:8px" }, t("gross.short")) : null)),
        h("td", { class: "num" }, ev?.status === "done" ? h("span", { class: `score ${scoreClass(ev.total)}` }, fmt(ev.total)) : h("span", { class: "muted" }, "—")),
        h("td", {}, ev?.open_appeals ? h("span", { class: "status-pill open" }, ev.open_appeals) : null));
    }))));
}

/** Set summary: how the operators in the chosen set did, criterion by criterion. Segment width = the
 * criterion's weight, fill = average share of its points earned where it applied (N/A excluded).
 * A click narrows the table to the dialogs that lost points on that criterion. */
function setSummary() {
  const rows = scopeRows();
  const done = rows.filter((c) => c.latest_evaluation?.status === "done");
  if (!done.length) return null;
  const f = state.filters;
  const totals = done.map((c) => c.latest_evaluation.total).filter((v) => v != null);
  const avg = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : null;
  const gross = done.filter((c) => c.latest_evaluation.gross_error).length;
  const ref = state.checklists.find((c) => c.id === "chat_default_v1") || state.checklists[0];
  const crits = (ref?.criteria || []).filter((c) => c.type !== "flag" && c.weight > 0);
  const segs = crits.map((c) => {
    const vals = done.map((r) => r.latest_evaluation.criteria?.[c.id]).filter((v) => v !== undefined);
    const applied = vals.filter((v) => v != null);
    const mean = applied.length ? applied.reduce((a, b) => a + b, 0) / applied.length : null;
    const losers = done.filter((r) => lostPoints(r, c.id)).length;
    const pct = mean == null ? "N/A" : `${Math.round(mean * 100)}%`;
    const tip = t("sum.critTip", { name: c.name, pct, n: applied.length, na: vals.length - applied.length, lost: losers });
    return h("button", { type: "button", class: `cs ${mean == null ? "na" : mean >= 0.995 ? "full" : ""}`,
      style: `flex-grow:${c.weight};--f:${mean == null ? 0 : (mean * 100).toFixed(1)}%`, title: tip, "aria-label": tip,
      "aria-pressed": String(f.crit === c.id), disabled: !losers && f.crit !== c.id,
      onclick: () => { f.crit = f.crit === c.id ? "" : c.id; refreshTable(); } },
      h("i", { "aria-hidden": "true" }), h("span", { class: "cs-id" }, c.id), h("span", { class: "cs-v" }, pct));
  });
  const active = crits.find((c) => c.id === f.crit);
  return h("section", { class: "set-summary", "aria-label": t("sum.label") },
    h("div", { class: "facts" },
      h("p", {}, h("b", { class: "fig" }, `${done.length}`), h("span", {}, t("sum.evaluated", { total: rows.length }))),
      avg != null ? h("p", {}, h("b", { class: `fig ${scoreClass(avg)}` }, fmt(avg)), h("span", {}, t("sum.avg"))) : null,
      h("p", {}, h("b", { class: `fig ${gross ? "lo" : ""}` }, `${gross}`), h("span", {}, t("sum.gross")))),
    h("div", { class: "crit-strip", role: "group", "aria-label": t("sum.stripLabel") }, segs),
    h("p", { class: "strip-note" }, active
      ? [t("sum.filterOn", { id: active.id, name: active.name }), " ",
         h("button", { type: "button", class: "linkish", onclick: () => { f.crit = ""; refreshTable(); } }, t("sum.clear"))]
      : t("sum.hint")));
}

function refreshTable() {
  if (!dialogsRefs) return;
  dialogsRefs.tableHost.replaceChildren(dialogsTable());
  dialogsRefs.summaryHost.replaceChildren(...[setSummary()].filter(Boolean));
}

/** Job poll on the dialogs page: refresh the strip always, the table only when focus is not inside it. */
function refreshDialogsLive() {
  if (!dialogsRefs || !document.body.contains(dialogsRefs.jobsHost)) return;
  dialogsRefs.jobsHost.replaceChildren(...[jobStrip()].filter(Boolean));
  if (!dialogsRefs.tableHost.contains(document.activeElement)) refreshTable();
  updateEvalButton();
}

function dialogsView() {
  const f = state.filters;
  const groupInput = h("input", { class: "input", value: state.groupDraft ?? store.get("group", "DEV"), style: "width:130px",
    oninput: (e) => { state.groupDraft = e.target.value; } });
  const fileInput = h("input", { type: "file", accept: ".json,application/json", hidden: true, onchange: async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const res = await api("/api/conversations/upload", { method: "POST", body: await file.text() });
      toast(t("toast.uploaded", { ids: res.imported.join(", ") }), "ok");
      await refreshConversations(); refreshTable();
    } catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); }
    e.target.value = "";
  } });
  const doImport = async (btn) => {
    const g = groupInput.value.trim();
    if (!g) { groupInput.focus(); return; }
    btn.disabled = true;
    try {
      const res = await api(`/api/datasets/${encodeURIComponent(g)}/import`, { method: "POST" });
      store.set("group", g);
      state.filters.group = g; // show the set just imported, not everything ever loaded
      toast(t("toast.imported", { n: res.imported }), "ok");
      await refreshConversations(); route();
      return;
    } catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); }
    btn.disabled = false;
  };
  const evalLabel = h("span", {});
  const evalBtn = h("button", { class: "btn primary", type: "button", icon: "run",
    onclick: async (e) => {
      e.currentTarget.disabled = true;
      try { await startJob(selectedIds(), state.checklistChoice); state.selected.clear(); refreshTable(); }
      catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); }
      updateEvalButton();
    } }, evalLabel);
  const jobsHost = h("div", { class: "jobs-host" }, jobStrip());
  const tableHost = h("div", { class: "table-host" }, dialogsTable());
  const summaryHost = h("div", { class: "summary-host" }, setSummary());
  dialogsRefs = { evalBtn, evalLabel, jobsHost, tableHost, summaryHost };
  updateEvalButton();
  const field = (label, control, cls = "") => h("label", { class: `field ${cls}` }, h("span", {}, label), control);
  const groups = [...new Set(state.conversations.map((c) => c.group_id).filter(Boolean))].sort();
  if (f.group && !groups.includes(f.group)) f.group = "";
  const resultsHref = () => `${API}/api/results?group_id=${encodeURIComponent(f.group)}&download=true`;
  const resultsLink = h("a", { class: "btn", icon: "download", href: f.group ? resultsHref() : "#", download: `results-${f.group}.json`,
    hidden: !f.group, title: t("dialogs.resultsHint") }, t("dialogs.results"));
  const groupSelect = h("select", { class: "input", onchange: (e) => {
    f.group = e.target.value;
    f.crit = "";
    resultsLink.hidden = !f.group;
    if (f.group) { resultsLink.href = resultsHref(); resultsLink.setAttribute("download", `results-${f.group}.json`); }
    refreshTable(); updateEvalButton();
  } }, h("option", { value: "" }, t("dialogs.allGroups")), groups.map((g) => h("option", { value: g, selected: g === f.group }, g)));

  return [
    h("div", { class: "topbar" }, h("div", {}, h("h1", {}, t("dialogs.title")), h("div", { class: "sub" }, t("dialogs.sub")))),
    h("div", { class: "toolbar" },
      field(t("dialogs.group"), groupInput),
      h("button", { class: "btn", type: "button", icon: "download", onclick: (e) => doImport(e.currentTarget) }, t("dialogs.import")),
      h("button", { class: "btn", type: "button", icon: "upload", onclick: () => fileInput.click() }, t("dialogs.upload")), fileInput,
      h("div", { class: "sep" }),
      field(t("dialogs.show"), groupSelect),
      field(t("dialogs.search"), h("input", { class: "input", type: "search", value: f.q, placeholder: "p1-ru-001",
        oninput: (e) => { f.q = e.target.value; refreshTable(); } }), "grow"),
      field(t("dialogs.channel"), h("select", { class: "input", onchange: (e) => { f.channel = e.target.value; f.crit = ""; refreshTable(); } },
        [["", t("dialogs.all")], ["agent chat", t("dialogs.chat")], ["voice call", t("dialogs.call")]].map(([v, l]) => h("option", { value: v, selected: f.channel === v }, l)))),
      field(t("dialogs.language"), h("select", { class: "input", onchange: (e) => { f.language = e.target.value; f.crit = ""; refreshTable(); } },
        [["", t("dialogs.all")], ["RU", "RU"], ["KZ", "KZ"], ["EN", "EN"]].map(([v, l]) => h("option", { value: v, selected: f.language === v }, l))))),
    h("div", { class: "actionbar" },
      field(t("dialogs.checklist"), h("select", { class: "input", onchange: (e) => { state.checklistChoice = e.target.value; } },
        h("option", { value: "" }, t("dialogs.autoHint")),
        state.checklists.map((c) => h("option", { value: c.id, selected: c.id === state.checklistChoice }, `${c.name}, v${c.version}`)))),
      evalBtn, h("div", { class: "spacer" }), resultsLink),
    jobsHost,
    summaryHost,
    tableHost,
  ];
}

// ---------- evaluation card ----------
function holdFlags(transcript) {
  let hold = false;
  return transcript.map((tu) => {
    const flag = hold && tu.speaker === "operator";
    if (tu.speaker === "system") {
      const low = tu.text.toLowerCase();
      if (low.includes("больше не находится на удержании") || low.includes("no longer on hold")) hold = false;
      else if (low.includes("на удержании") || low.includes("on hold")) hold = true;
    }
    return flag;
  });
}

function transcriptPane(conv) {
  const isCall = conv.channel === "voice call";
  const audio = conv.has_audio ? h("audio", { controls: true, preload: "none", src: `${API}/api/conversations/${encodeURIComponent(conv.conversation_id)}/audio` }) : null;
  const holds = holdFlags(conv.transcript);
  const list = h("div", { class: "transcript", id: "transcript" }, conv.transcript.map((tu, i) => {
    const time = isCall && tu.audio_offset != null
      ? `${String(Math.floor(tu.audio_offset / 60)).padStart(2, "0")}:${String(Math.floor(tu.audio_offset % 60)).padStart(2, "0")}`
      : isCall ? "" : tu.timestamp;
    return h("div", { class: `turn ${tu.speaker} ${holds[i] ? "hold" : ""}`, id: `turn-${tu.index}`, "data-hold": holds[i] ? t("turn.hold") : false },
      h("div", { class: "idx" }, tu.index + 1),
      h("div", {},
        h("div", { class: "who" }, t(`speaker.${tu.speaker}`), time ? h("time", {}, time) : null,
          audio && tu.audio_offset != null ? h("button", { class: "seek", type: "button", title: t("turn.play"), "aria-label": t("turn.play"), icon: "play",
            onclick: () => { audio.currentTime = tu.audio_offset; audio.play().catch(() => {}); } }) : null),
        h("div", { class: "txt" }, tu.text)));
  }));
  const pane = h("section", { class: "pane transcript-pane", "aria-label": t("card.transcript") },
    h("div", { class: "pane-head" }, h("h2", {}, t("card.transcript")),
      isCall ? h("span", { class: "tag call", icon: "phone" }, t("dialogs.call")) : h("span", { class: "tag", icon: "chat" }, t("dialogs.chat")),
      h("span", { class: "tag lang" }, conv.language), h("span", { class: "muted", style: "margin-left:auto;font-size:.85rem" }, `${conv.transcript.length} · ${t("col.turns").toLowerCase()}`)),
    audio ? h("div", { class: "player" }, audio) : null, list);
  pane.focusTurns = (indices, range, seek) => {
    list.querySelectorAll(".lit,.range").forEach((el) => el.classList.remove("lit", "range"));
    const targets = [];
    if (range) for (let i = range[0]; i <= range[1]; i++) { const el = list.querySelector(`#turn-${i}`); if (el) { el.classList.add("range"); targets.push(el); } }
    for (const i of indices) { const el = list.querySelector(`#turn-${i}`); if (el) { el.classList.add("lit"); targets.unshift(el); } }
    if (targets[0]) {
      const box = list.getBoundingClientRect();
      const r = targets[0].getBoundingClientRect();
      list.scrollTo({ top: list.scrollTop + r.top - box.top - box.height / 3, behavior: "smooth" });
    }
    if (seek && audio && indices.length) {
      const tu = conv.transcript.find((x) => x.index === indices[0]);
      if (tu?.audio_offset != null) audio.currentTime = tu.audio_offset;
    }
  };
  return pane;
}

/** Signature move: effective weights as a ribbon; N/A collapses, decisions animate from original to final. */
function ribbon(checklist, from, to, onPick) {
  const crits = checklist.criteria.filter((c) => c.type !== "flag");
  const byId = (s) => Object.fromEntries(s.criteria.map((x) => [x.criterion_id, x]));
  const a = byId(from), b = byId(to);
  const vars = (x) => {
    const na = x.answer === "na" || !x.effective_weight;
    const frac = na ? 0 : Math.max(0, Math.min(1, (x.points || 0) / x.effective_weight));
    return { na, w: na ? 0 : x.effective_weight, f: frac };
  };
  const segs = crits.map((c) => {
    const s = vars(a[c.id]);
    const el = h("button", { type: "button", class: `seg-w ${s.na ? "na" : ""} ${s.f >= 0.999 ? "full" : ""} ${s.f <= 0 ? "zero" : ""}`,
      style: `--w:${s.w};--f:${(s.f * 100).toFixed(1)}%`,
      title: `${c.id} · ${c.name}: ${s.na ? "N/A" : `${fmt(a[c.id].points)} / ${fmt(a[c.id].effective_weight)}`}`,
      onclick: () => onPick(c.id) }, h("i"), h("b", {}, c.id.replace(/^c0?/, "")));
    el.dataset.id = c.id;
    return el;
  });
  const wrap = h("div", { class: "ribbon-wrap" },
    h("div", { class: "ribbon", role: "group", "aria-label": t("card.ribbon") }, segs),
    h("div", { class: "ribbon-legend" },
      h("span", {}, h("i", { style: "background:var(--fr-green)" }), t("legend.earned")),
      h("span", {}, h("i", { style: "background:var(--ground-2);border:1px solid var(--steel-2)" }), t("legend.lost")),
      h("span", {}, h("i", { style: "background:repeating-linear-gradient(135deg,var(--panel-2) 0 3px,var(--ground) 3px 6px);border:1px dashed var(--steel-3)" }), t("legend.na"))));
  if (from !== to) {
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => {
      for (const el of segs) {
        const s = vars(b[el.dataset.id]);
        el.style.setProperty("--w", s.w);
        el.style.setProperty("--f", `${(s.f * 100).toFixed(1)}%`);
        el.classList.toggle("na", s.na); el.classList.toggle("full", s.f >= 0.999); el.classList.toggle("zero", s.f <= 0);
      }
    }, 450)));
  }
  return wrap;
}

function critChanged(a, b) {
  const keys = ["name", "type", "weight", "na_allowed", "description", "na_rule", "guidance"];
  return keys.some((k) => JSON.stringify(a[k] ?? null) !== JSON.stringify(b[k] ?? null)) || JSON.stringify(a.scale ?? null) !== JSON.stringify(b.scale ?? null);
}

function criterionRow(ev, crit, pane, appeals, rerender, prev) {
  const res = ev.evaluation.results.find((r) => r.criterion_id === crit.id);
  const prevCrit = prev?.checklist?.criteria.find((x) => x.id === crit.id);
  const prevAns = prev?.evaluation?.results.find((r) => r.criterion_id === crit.id)?.answer;
  const original = ev.evaluation.results.find((r) => r.criterion_id === crit.id)?.answer;
  const finalAnswer = ev.final_answers[crit.id];
  const sc = ev.final_score.criteria.find((x) => x.criterion_id === crit.id);
  const mine = appeals.filter((a) => a.criterion_id === crit.id);
  const open = mine.find((a) => a.status === "open");
  const pts = crit.type === "flag" ? null
    : finalAnswer === "na" ? h("span", { class: "pts muted" }, "N/A")
    : h("span", { class: "pts" }, fmt(sc?.points), h("small", {}, ` / ${fmt(sc?.effective_weight)}`));
  const appealBox = h("div", { class: "appeal-box", hidden: true });
  const comment = h("textarea", { class: "input", rows: 3, placeholder: t("card.appealPh"), "aria-label": t("card.appealPh") });
  appealBox.append(comment, h("div", { class: "crit-actions" },
    h("button", { class: "btn primary sm", type: "button", onclick: async (e) => {
      if (!comment.value.trim()) { comment.focus(); return; }
      e.currentTarget.disabled = true;
      try {
        await api(`/api/evaluations/${ev.id}/appeals`, { method: "POST", body: JSON.stringify({ criterion_id: crit.id, comment: comment.value.trim() }) });
        toast(t("toast.appealSent"), "ok");
        await refreshConversations(); renderRail(); rerender();
      } catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); e.currentTarget.disabled = false; }
    } }, t("card.send")),
    h("button", { class: "btn ghost sm", type: "button", onclick: () => { appealBox.hidden = true; } }, t("card.cancel"))));

  return h("article", { class: "crit", id: `crit-${crit.id}` },
    h("div", { class: "crit-top" },
      h("div", { class: "crit-name" }, `${crit.id} · ${crit.name}`,
        h("small", {}, crit.type === "flag" ? t("type.flag") : t("card.weight", { w: crit.weight }),
          prev && !prevCrit ? h("span", { class: "status-pill new" }, t("card.newCrit")) : null,
          prevCrit && critChanged(prevCrit, crit) ? h("span", { class: "status-pill new" }, t("card.changedCrit")) : null,
          prevAns !== undefined && prevAns !== original ? h("span", { class: "was-answer" }, `${t("card.was")}: ${answerLabel(prevAns, prevCrit || crit)}`) : null)),
      original !== finalAnswer ? h("span", { class: `ans ${answerClass(original, crit)}`, style: "opacity:.45;text-decoration:line-through" }, answerLabel(original, crit)) : null,
      h("span", { class: `ans ${answerClass(finalAnswer, crit)}` }, answerLabel(finalAnswer, crit)),
      pts),
    res?.explanation ? h("p", { class: "expl" }, res.explanation) : null,
    (res?.evidence?.length || res?.checked_range) ? h("div", { class: "evidence" },
      h("span", { class: "muted", style: "font-size:.8rem" }, t("card.evidence")),
      (res.evidence || []).map((i) => h("button", { type: "button", class: "ev", onclick: () => { pane.focusTurns([i], null, true); focusCrit(crit.id); } }, `#${i + 1}`)),
      res.checked_range ? h("button", { type: "button", class: "ev range", title: t("card.checked"),
        onclick: () => { pane.focusTurns([], res.checked_range, false); focusCrit(crit.id); } }, `${t("card.checked")} #${res.checked_range[0] + 1}–${res.checked_range[1] + 1}`) : null) : null,
    mine.filter((a) => a.status === "resolved").map((a) => h("div", { class: "decision-line" },
      h("b", {}, a.decision === "change"
        ? t("card.decisionChange", { a: fmt(a.total_before), b: fmt(a.total_after) })
        : t("card.decisionConfirm")),
      ` · ${t("card.reason")}: ${a.reason}`)),
    open ? h("div", { class: "decision-line" }, h("span", { class: "status-pill open" }, t("card.appealOpen")), ` · ${open.comment}`) : null,
    state.role === "analyst" && !open ? h("div", { class: "crit-actions" },
      h("button", { class: "btn sm", type: "button", onclick: () => { appealBox.hidden = false; comment.focus(); } }, t("card.appeal"))) : null,
    appealBox);
}

function focusCrit(id) {
  document.querySelectorAll(".crit.focus").forEach((el) => el.classList.remove("focus"));
  document.querySelectorAll(".seg-w.active").forEach((el) => el.classList.remove("active"));
  document.getElementById(`crit-${id}`)?.classList.add("focus");
  document.querySelector(`.seg-w[data-id="${id}"]`)?.classList.add("active");
}

async function cardView(convId, evalId) {
  const conv = await api(`/api/conversations/${encodeURIComponent(convId)}`);
  const evals = conv.evaluations || [];
  const chosen = evalId || evals.at(-1)?.id;
  const ev = chosen ? await api(`/api/evaluations/${chosen}`) : null;
  // the evaluation before the chosen one: shows what a changed checklist or a re-run actually changed
  const chosenIdx = evals.findIndex((e) => e.id === chosen);
  const prevMeta = chosenIdx > 0 ? evals.slice(0, chosenIdx).reverse().find((e) => e.status === "done") : null;
  const prev = prevMeta && ev?.status === "done" ? await api(`/api/evaluations/${prevMeta.id}`).catch(() => null) : null;
  const pane = transcriptPane(conv);
  const rerender = () => route();
  const runningJob = state.jobs.find((j) => (j.status === "running" || j.status === "queued") && j.conversation_ids.includes(convId)
    && !(j.evaluation_ids || []).some((id) => evals.some((e) => e.id === id)));
  const checklistSelect = h("select", { class: "input", "aria-label": t("dialogs.checklist"), style: "max-width:240px" },
    h("option", { value: "" }, t("dialogs.auto")),
    state.checklists.map((c) => h("option", { value: c.id, selected: ev && c.id === ev.checklist_id }, `${c.name} · v${c.version}`)));
  const engineSelect = state.engineOptions.length > 1 ? h("select", { class: "input", "aria-label": t("engine.pick"), style: "max-width:220px" },
    state.engineOptions.map(([v, l]) => h("option", { value: v, selected: v === (state.engineChoice || "") }, l))) : null;
  const evalButton = state.role === "analyst" ? h("button", { class: "btn primary sm", type: "button", icon: "run", disabled: Boolean(runningJob),
    onclick: async (e) => {
      e.currentTarget.disabled = true;
      state.engineChoice = engineSelect?.value || "";
      try { await startJob([convId], checklistSelect.value, state.engineChoice); route(); } catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); e.currentTarget.disabled = false; }
    } }, ev ? t("card.reevaluate") : t("card.evaluate")) : null;

  const head = h("div", { class: "pane-head" },
    h("h2", {}, t("card.result")),
    evals.length > 1 ? h("select", { class: "input", style: "height:30px", "aria-label": t("card.version"),
      onchange: (e) => navigate(`#/c/${encodeURIComponent(convId)}/${e.target.value}`) },
      evals.map((e, i) => h("option", { value: e.id, selected: e.id === chosen },
        `${t("card.version")} ${i + 1} · ${e.checklist_id} v${e.checklist_version ?? "?"} · ${e.status === "done" ? fmt(e.total) : t("status.failed")}`))) : null,
    h("div", { style: "margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, state.role === "analyst" ? checklistSelect : null,
      state.role === "analyst" ? engineSelect : null, evalButton));

  const body = [];
  if (runningJob) {
    body.push(h("div", { class: "result-head" }, h("div", {}, h("div", { class: "pulse" })), h("div", {}, h("b", {}, t("card.evaluating")))),
      h("div", { style: "padding:16px;display:flex;flex-direction:column;gap:10px" }, [80, 92, 70, 86].map((w) => h("div", { class: "skeleton", style: `width:${w}%` }))));
  } else if (!ev) {
    body.push(h("div", { class: "empty" }, h("h3", {}, t("card.noEval")), h("p", {}, t("card.noEvalText"))));
  } else if (ev.status === "failed") {
    body.push(h("div", { style: "padding:16px" }, h("div", { class: "notice error" }, h("b", {}, t("card.failed")), h("div", {}, ev.error))));
  } else {
    const checklist = ev.checklist;
    const appeals = ev.appeal_details || [];
    const usage = ev.evaluation.usage || {};
    const fin = ev.final_score.total, orig = ev.score.total;
    body.push(
      h("div", { class: "result-head" },
        h("div", { class: `total score ${scoreClass(fin)}` }, fin == null ? "—" : fmt(fin), h("small", {}, "/ 100")),
        h("div", { class: "total-meta" },
          fin == null ? h("div", { class: "row" }, t("card.noTotal")) : null,
          orig !== fin ? h("div", { class: "row" }, t("card.originalWas"), h("span", { class: "was" }, fmt(orig))) : null,
          prev ? h("div", { class: "row" }, h("span", { class: "muted" }, t("card.prev")),
            `${fmt(prev.score.total)} → ${fmt(orig)}`, h("span", { class: "muted" }, `${prev.checklist.name} · v${prev.checklist_version}`)) : null,
          h("div", { class: "row" }, h("span", { class: "muted" }, t("card.checklist")), `${checklist.name} · v${ev.checklist_version}`),
          h("div", { class: "row" }, h("span", { class: "muted" }, t("card.engine")), ev.model || ev.engine || "—",
            usage.fallback_from ? h("span", { class: "status-pill open" }, t("card.fallback")) : null),
          h("div", { class: "row" }, h("span", { class: "muted" }, t("card.cost")), ev.cost_usd ? `$${ev.cost_usd}` : t("card.free"),
            h("span", { class: "muted" }, t("card.tokens", { p: usage.prompt_tokens ?? 0, o: usage.output_tokens ?? 0, t: usage.thinking_tokens ?? 0 })))),
        ev.final_score.gross_error ? h("div", { class: "gross-banner", icon: "warn" }, t("card.gross")) : null),
      ribbon(checklist, ev.score, ev.final_score, (id) => { focusCrit(id); document.getElementById(`crit-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }),
      ev.final_score.sections?.length ? h("div", { class: "sections", "aria-label": t("card.sections") },
        ev.final_score.sections.map((s) => h("span", { class: "section-chip" },
          checklist.sections.find((x) => x.id === s.section_id)?.name || s.section_id,
          h("b", {}, `${fmt(s.points)} / ${fmt(s.max_points)}`)))) : null,
      h("div", { class: "criteria" }, checklist.criteria.map((c) => criterionRow(ev, c, pane, appeals, rerender, prev))),
      state.role === "reviewer" ? h("div", { class: "tech" }, t("card.reviewerHint")) : null,
      h("details", { class: "tech" }, h("summary", {}, t("card.tech")),
        h("pre", {}, JSON.stringify({ engine: ev.engine, model: ev.model, usage, checklist_fingerprint: ev.checklist_fingerprint }, null, 2)),
        h("pre", {}, ev.evaluation.raw_response || "")));
  }

  return [
    h("div", { class: "topbar" },
      h("a", { href: "#/", class: "btn ghost sm", icon: "back" }, t("card.back")),
      h("div", {}, h("h1", {}, conv.conversation_id), h("div", { class: "sub" }, `${conv.group_id || ""} · ${conv.language} · ${conv.channel === "voice call" ? t("dialogs.call") : t("dialogs.chat")}`))),
    h("div", { class: "card-layout" }, pane, h("section", { class: "pane", "aria-label": t("card.result") }, head, body)),
  ];
}

// ---------- appeals ----------
/** Appeals need only each evaluation's checklist snapshot, which never changes: fetch once. */
async function evaluationCached(id) {
  if (!state.evalCache.has(id)) state.evalCache.set(id, api(`/api/evaluations/${id}`));
  return state.evalCache.get(id);
}

async function appealsView(tab) {
  const status = tab === "resolved" ? "resolved" : "open";
  const list = await api(`/api/appeals?status=${status}`);
  const items = await Promise.all(list.reverse().map(async (a) => ({ a, ev: await evaluationCached(a.evaluation_id) })));
  const tabs = h("div", { class: "seg", style: "width:max-content" },
    [["open", t("appeals.open")], ["resolved", t("appeals.resolved")]].map(([v, l]) =>
      h("button", { type: "button", "aria-pressed": String(v === status), onclick: () => navigate(`#/appeals/${v}`) }, l)));

  const card = ({ a, ev }) => {
    const crit = ev.checklist.criteria.find((c) => c.id === a.criterion_id);
    const fragment = h("div", { class: "fragment" }, (a.fragment || []).map((tu) =>
      h("div", { class: `turn ${tu.speaker} ${a.disputed.evidence.includes(tu.index) ? "lit" : ""}` },
        h("div", { class: "idx" }, tu.index + 1),
        h("div", {}, h("div", { class: "who" }, t(`speaker.${tu.speaker}`)), h("div", { class: "txt" }, tu.text)))));
    const left = h("div", { class: "left" },
      h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" },
        h("h3", {}, `${a.conversation_id} · ${crit?.id} ${crit?.name || ""}`),
        h("a", { href: `#/c/${encodeURIComponent(a.conversation_id)}/${a.evaluation_id}`, class: "btn ghost sm" }, t("appeals.openCard"))),
      h("div", { style: "display:flex;gap:8px;align-items:center;margin-top:6px" }, h("span", { class: "muted" }, t("appeals.disputed")),
        h("span", { class: `ans ${answerClass(a.disputed.answer, crit)}` }, answerLabel(a.disputed.answer, crit))),
      h("div", { class: "quote" }, h("b", {}, `${t("appeals.modelSaid")}: `), a.disputed.explanation),
      h("div", { class: "quote" }, h("b", {}, `${t("appeals.comment")}: `), a.comment),
      a.fragment?.length ? [h("div", { class: "muted", style: "margin-top:10px;font-size:.82rem" }, t("appeals.fragment")), fragment] : null);

    let right;
    if (a.status === "resolved") {
      right = h("div", { class: "decide" },
        h("div", { class: "muted" }, t("appeals.decided")),
        h("div", {}, a.decision === "change"
          ? [h("span", { class: `ans ${answerClass(a.disputed.answer, crit)}`, style: "opacity:.5" }, answerLabel(a.disputed.answer, crit)), " → ",
             h("span", { class: `ans ${answerClass(a.new_answer, crit)}` }, answerLabel(a.new_answer, crit))]
          : h("b", {}, t("appeals.confirm"))),
        h("div", { class: "quote" }, h("b", {}, `${t("card.reason")}: `), a.reason),
        h("div", {}, h("div", { class: "muted" }, t("appeals.totalChange")),
          h("div", { class: "delta" }, fmt(a.total_before), h("span", { class: "arrow" }, "→"),
            h("span", { class: `score ${scoreClass(a.total_after)}` }, fmt(a.total_after)))));
    } else if (state.role !== "reviewer") {
      right = h("div", { class: "decide" }, h("div", { class: "notice" }, t("appeals.reviewerOnly")));
    } else {
      const options = allowedAnswers(crit).filter((x) => String(x) !== String(a.disputed.answer));
      const mode = { value: "confirm" };
      const sel = h("select", { class: "input", disabled: true }, options.map((o) => h("option", { value: String(o) }, answerLabel(o, crit))));
      const reason = h("textarea", { class: "input", rows: 3, placeholder: t("appeals.reason"), "aria-label": t("appeals.reason") });
      const radio = (v, label) => h("label", { class: "check" },
        h("input", { type: "radio", name: `d-${a.id}`, value: v, checked: v === "confirm", onchange: () => { mode.value = v; sel.disabled = v !== "change"; } }), label);
      right = h("div", { class: "decide" },
        h("div", { class: "row" }, radio("confirm", t("appeals.confirm"))),
        h("div", { class: "row" }, radio("change", t("appeals.change")), sel),
        reason,
        h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", onclick: async (e) => {
          if (!reason.value.trim()) { reason.focus(); return; }
          e.currentTarget.disabled = true;
          const raw = sel.value;
          const newAnswer = /^-?\d+$/.test(raw) ? Number(raw) : raw;
          try {
            const res = await api(`/api/appeals/${a.id}/decision`, { method: "POST",
              body: JSON.stringify({ decision: mode.value, reason: reason.value.trim(), ...(mode.value === "change" ? { new_answer: newAnswer } : {}) }) });
            toast(t("toast.decided", { a: fmt(res.appeal.total_before), b: fmt(res.appeal.total_after) }), "ok");
            await refreshConversations(); renderRail(); route();
          } catch (err) { toast(`${t("err.prefix")}: ${err.message}`, "error"); e.currentTarget.disabled = false; }
        } }, t("appeals.decide"))));
    }
    return h("article", { class: "appeal" }, left, h("div", {}, right));
  };

  return [
    h("div", { class: "topbar" }, h("div", {}, h("h1", {}, t("appeals.title")), h("div", { class: "sub" }, t("appeals.sub"))), h("div", { class: "spacer" }), tabs),
    items.length ? h("div", { class: "appeal-list" }, items.map(card))
      : h("div", { class: "table-wrap" }, h("div", { class: "empty" },
        h("h3", {}, status === "open" ? t("appeals.emptyOpen") : t("appeals.emptyResolved")),
        status === "open" ? h("p", {}, t("appeals.emptyOpenText")) : null)),
  ];
}

// ---------- checklist editor ----------
let draft = null;
let draftDirty = false;

function checklistsView(id) {
  const chosen = id || draft?.id || state.checklists[0]?.id;
  if (!draft || draft.id !== chosen || !draftDirty) {
    const src = state.checklists.find((c) => c.id === chosen);
    draft = src ? structuredClone(src) : null;
    draftDirty = false;
  }
  const mark = () => { draftDirty = true; dirtyNote.hidden = false; weightNote.replaceWith(weightNote = weights()); };
  const dirtyNote = h("span", { class: "status-pill open", hidden: !draftDirty }, t("cl.dirty"));
  const weights = () => {
    const sum = draft.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + Number(c.weight || 0), 0);
    return h("div", { class: "weights" }, t("cl.weightSum", { n: fmt(sum) }), sum !== 100 ? h("span", { class: "bad" }, `· ${t("cl.weightNote")}`) : null);
  };
  let weightNote = draft ? weights() : null;
  const errorBox = h("div", { class: "notice error", hidden: true });

  const save = async (asNew, stay = false) => {
    errorBox.hidden = true;
    const badStep = draft.criteria.find((c) => c.type === "scale" && (c.scale || []).some((s) => String(s.value).trim() === ""));
    if (badStep) { errorBox.textContent = t("cl.stepEmpty", { id: badStep.id }); errorBox.hidden = false; return null; }
    const body = structuredClone(draft);
    for (const c of body.criteria) {
      c.weight = Number(c.weight) || 0;
      if (c.type !== "scale") delete c.scale;
      if (c.type === "flag") { c.weight = 0; c.na_allowed = false; }
      if (!c.na_allowed) delete c.na_rule;
      if (c.scale) c.scale = c.scale.map((s) => ({ value: Number(s.value), description: s.description }));
      if (!c.section) delete c.section;
      if (!c.guidance) delete c.guidance;
    }
    try {
      let saved;
      if (asNew) {
        body.id = newIdInput.value.trim();
        saved = await api("/api/checklists", { method: "POST", body: JSON.stringify(body) });
      } else {
        saved = await api(`/api/checklists/${encodeURIComponent(body.id)}`, { method: "PUT", body: JSON.stringify(body) });
      }
      toast(t("toast.saved", { v: saved.version }), "ok");
      state.checklists = await api("/api/checklists");
      draft = null; draftDirty = false;
      if (!stay) navigate(`#/checklists/${encodeURIComponent(saved.id)}`);
      return saved;
    } catch (err) { errorBox.textContent = err.message; errorBox.hidden = false; errorBox.scrollIntoView({ block: "center" }); return null; }
  };
  const newIdInput = h("input", { class: "input", placeholder: "chat_control_v1", "aria-label": t("cl.newId"), style: "width:220px" });

  const critEditor = (c, i) => {
    const bind = (key, transform = (v) => v) => (e) => { c[key] = transform(e.target.type === "checkbox" ? e.target.checked : e.target.value); mark(); };
    const steps = h("div", { class: "steps" });
    const renderSteps = () => steps.replaceChildren(...(c.scale || []).map((s, si) => h("div", { class: "step-row" },
      h("input", { class: "input", type: "number", value: s.value, "aria-label": t("cl.steps"), oninput: (e) => { s.value = e.target.value; mark(); } }),
      h("input", { class: "input", value: s.description, "aria-label": t("cl.steps"), oninput: (e) => { s.description = e.target.value; mark(); } }),
      h("button", { class: "btn ghost sm", type: "button", onclick: () => { c.scale.splice(si, 1); renderSteps(); mark(); } }, t("cl.remove")))),
      h("button", { class: "btn sm", type: "button", style: "align-self:flex-start", onclick: () => { (c.scale ||= []).push({ value: 0, description: "" }); renderSteps(); mark(); } }, t("cl.addStep")));
    renderSteps();
    const scaleBlock = h("div", { class: "span-12", hidden: c.type !== "scale" }, h("div", { class: "field" }, h("span", {}, t("cl.steps")), steps));
    const naRule = h("label", { class: "field span-12", hidden: !c.na_allowed }, h("span", {}, t("cl.naRule")),
      h("input", { class: "input", value: c.na_rule || "", oninput: bind("na_rule") }));
    return h("div", { class: "crit-edit" },
      h("div", { class: "crit-edit-head" }, h("b", {}, c.id || "—"), h("span", { style: "flex:1" }, c.name),
        h("button", { class: "btn ghost sm danger", type: "button", onclick: () => {
          if (!window.confirm(t("cl.removeConfirm", { id: c.id || "—" }))) return;
          draft.criteria.splice(i, 1); mark(); route();
        } }, t("cl.remove"))),
      h("div", { class: "form-grid" },
        h("label", { class: "field span-2" }, h("span", {}, t("cl.critId")), h("input", { class: "input", value: c.id, oninput: bind("id") })),
        h("label", { class: "field span-3" }, h("span", {}, t("cl.critName")), h("input", { class: "input", value: c.name, oninput: bind("name") })),
        h("label", { class: "field span-3" }, h("span", {}, t("cl.section")),
          h("select", { class: "input", onchange: bind("section") }, h("option", { value: "" }, t("cl.noSection")),
            draft.sections.map((s) => h("option", { value: s.id, selected: s.id === c.section }, s.name)))),
        h("label", { class: "field span-2" }, h("span", {}, t("cl.type")),
          h("select", { class: "input", onchange: (e) => {
            c.type = e.target.value;
            if (c.type === "scale" && !c.scale?.length) c.scale = [{ value: 10, description: "" }, { value: 0, description: "" }];
            if (c.type === "flag") { c.weight = 0; c.na_allowed = false; }
            mark(); route();
          } }, ["binary", "scale", "flag"].map((ty) => h("option", { value: ty, selected: c.type === ty }, t(`type.${ty}`))))),
        h("label", { class: "field span-2" }, h("span", {}, t("cl.weight")),
          h("input", { class: "input", type: "number", min: 0, value: c.weight, disabled: c.type === "flag", oninput: bind("weight") })),
        h("label", { class: "field span-12" }, h("span", {}, t("cl.description")),
          h("textarea", { class: "input", rows: 2, oninput: bind("description") }, c.description || "")),
        h("label", { class: "check span-12" }, h("input", { type: "checkbox", checked: Boolean(c.na_allowed), disabled: c.type === "flag",
          onchange: (e) => { c.na_allowed = e.target.checked; naRule.hidden = !c.na_allowed; mark(); } }), t("cl.naAllowed")),
        naRule, scaleBlock,
        h("label", { class: "field span-12" }, h("span", {}, t("cl.guidance")),
          h("textarea", { class: "input", rows: 3, oninput: bind("guidance") }, c.guidance || ""))));
  };

  return [
    h("div", { class: "topbar" }, h("div", {}, h("h1", {}, t("cl.title")), h("div", { class: "sub" }, t("cl.sub")))),
    h("div", { class: "editor" },
      h("div", { class: "cl-list" }, state.checklists.map((c) => h("button", { type: "button", "aria-current": String(c.id === chosen),
        onclick: () => {
          if (draftDirty && c.id !== draft?.id && !window.confirm(t("cl.discard"))) return;
          draft = null; draftDirty = false; navigate(`#/checklists/${encodeURIComponent(c.id)}`);
        } },
        h("b", {}, c.name), h("small", {}, `${c.id} · ${t("cl.version", { v: c.version })} · ${t(`cl.channel.${c.channel}`)}`)))),
      draft ? h("div", {},
        errorBox,
        h("div", { class: "form-grid", style: "margin-bottom:14px" },
          h("label", { class: "field span-6" }, h("span", {}, t("cl.name")), h("input", { class: "input", value: draft.name, oninput: (e) => { draft.name = e.target.value; mark(); } })),
          h("label", { class: "field span-3" }, h("span", {}, t("cl.channel")),
            h("select", { class: "input", onchange: (e) => { draft.channel = e.target.value; mark(); } },
              ["chat", "call", "any"].map((ch) => h("option", { value: ch, selected: draft.channel === ch }, t(`cl.channel.${ch}`))))),
          h("div", { class: "field span-3" }, h("span", {}, t("cl.id")), h("div", { style: "padding-top:7px" }, `${draft.id} · ${t("cl.version", { v: draft.version })}`))),
        h("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:10px" }, h("h2", { style: "margin:0;font-size:1.05rem" }, t("cl.criteria")), weightNote, dirtyNote),
        draft.criteria.map(critEditor),
        h("button", { class: "btn", type: "button", style: "margin-top:12px", onclick: () => {
          const used = new Set(draft.criteria.map((c) => c.id));
          let n = draft.criteria.length + 1;
          while (used.has(`c${String(n).padStart(2, "0")}`)) n++;
          draft.criteria.push({ id: `c${String(n).padStart(2, "0")}`, name: "", type: "binary", weight: 10, na_allowed: false, description: "", section: draft.sections[0]?.id });
          mark(); route();
        } }, t("cl.add")),
        h("div", { class: "sticky-actions" },
          h("button", { class: "btn", type: "button", onclick: async () => {
            let id = draft.id;
            if (draftDirty) { const saved = await save(false, true); if (!saved) return; id = saved.id; }
            state.checklistChoice = id;
            toast(t("cl.applied"), "ok");
            navigate("#/");
          } }, t("cl.apply")),
          newIdInput,
          h("button", { class: "btn", type: "button", onclick: () => save(true) }, t("cl.saveAs")),
          h("button", { class: "btn primary", type: "button", onclick: () => save(false) }, t("cl.save")))) : null),
  ];
}

// ---------- router ----------
let renderSeq = 0;
let lastView = null;
async function route() {
  const seq = ++renderSeq;
  renderRail();
  try {
    const [, view = "", a, b] = location.hash.replace(/^#/, "").split("/").map(decodeURIComponent);
    let content;
    let onJobs = () => {};
    if (view === "c" && a) {
      content = await cardView(a, b);
      onJobs = (finished) => { if (finished.some((j) => j.conversation_ids.includes(a))) route(); };
    } else if (view === "appeals") {
      content = await appealsView(a);
    } else if (view === "checklists") {
      content = checklistsView(a);
    } else {
      content = dialogsView();
      onJobs = () => refreshDialogsLive();
    }
    if (seq !== renderSeq) return; // a newer navigation won; drop this render and its job hook
    onJobsChanged = onJobs;
    $main.replaceChildren(...[content].flat().filter(Boolean));
    const viewKey = `${view}/${a || ""}`;
    if (lastView !== null && lastView !== viewKey) $main.focus({ preventScroll: true });
    if (lastView !== viewKey) window.scrollTo(0, 0);
    lastView = viewKey;
  } catch (err) {
    if (seq !== renderSeq) return;
    $main.replaceChildren(h("div", { class: "notice error" }, `${t("err.prefix")}: ${err.message}`));
  }
}

async function boot() {
  if (store.get("big", "")) document.documentElement.classList.add("big");
  renderRail();
  $main.replaceChildren(h("div", { style: "display:flex;flex-direction:column;gap:12px;max-width:700px" },
    [60, 90, 75, 85].map((w) => h("div", { class: "skeleton", style: `width:${w}%;height:18px` }))));
  try {
    const [, checklists, engines] = await Promise.all([refreshConversations(), api("/api/checklists"), api("/api/engines").catch(() => null)]);
    state.checklists = checklists;
    state.engine = engines?.configured?.[0]?.split(":").slice(1).join(":") || "";
    const configured = engines?.configured || [];
    state.engineOptions = [["", t("engine.auto")], ...configured.map((spec) => [spec, spec.startsWith("ollama:") ? `${spec} · ${t("engine.local")}` : spec]),
      ...((engines?.available || []).includes("ollama") && !configured.some((s) => s.startsWith("ollama:")) ? [["ollama:qwen3:1.7b", t("engine.local")]] : [])];
  } catch (err) {
    $main.replaceChildren(h("div", { class: "notice error" }, err.message));
    return;
  }
  window.addEventListener("hashchange", route);
  window.addEventListener("beforeunload", (e) => { if (draftDirty) e.preventDefault(); });
  await refreshJobs(); // first paint already knows which dialogs are being evaluated
  await route();
}

boot();
