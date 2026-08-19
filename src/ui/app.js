/* Demo UI.
 *
 * Deliberately shows the *reasoning*, not just the answer. The submission is
 * judged on surfacing entity resolution, contradictions and abstention
 * (CLAUDE.md §6), and none of those are visible from an answer string alone —
 * so each gets its own panel driven by the trace the API returns.
 *
 * No build step and no CDN: cytoscape is vendored locally so the demo cannot
 * fail because a network is flaky while recording.
 */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let cy = null;

/* ------------------------------------------------------------------ */
/* header stats                                                        */
/* ------------------------------------------------------------------ */
async function loadStats() {
  try {
    const s = await (await fetch("/api/stats")).json();
    const box = $("stats");
    box.innerHTML = "";
    [["documents", "documents"], ["entities", "people"], ["aliases", "aliases"]]
      .forEach(([key, label]) => {
        if (!s[key]) return;
        const d = el("div", "stat");
        d.appendChild(el("b", null, s[key].toLocaleString()));
        d.appendChild(el("span", null, label));
        box.appendChild(d);
      });
  } catch { /* header stats are decorative; never block the page on them */ }
}

/* ------------------------------------------------------------------ */
/* ask                                                                 */
/* ------------------------------------------------------------------ */
async function ask(question) {
  $("question").value = question;
  $("ask-btn").disabled = true;
  $("ask-btn").innerHTML = '<span class="spin"></span>';
  $("empty").classList.add("hidden");

  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    data.question = question;
    render(data);
  } catch (err) {
    $("result").classList.remove("hidden");
    $("answer").textContent = `Request failed: ${err.message}`;
    $("answer").className = "answer abstained";
  } finally {
    $("ask-btn").disabled = false;
    $("ask-btn").textContent = "Ask";
  }
}

function render(data) {
  $("result").classList.remove("hidden");
  const trace = data.trace || {};

  $("route-pill").textContent = trace.route || "lookup";

  const grade = $("grade-pill");
  if (data.abstained) {
    grade.textContent = "abstained";
    grade.className = "pill warn";
  } else {
    grade.textContent = "answered";
    grade.className = "pill good";
  }

  const bits = [];
  if (data.document_ids?.length) bits.push(`${data.document_ids.length} sources cited`);
  if (trace.llm_calls) bits.push(`${trace.llm_calls} LLM calls`);
  if (trace.retries) bits.push(`${trace.retries} retry`);
  $("answer-meta").textContent = bits.join("  ·  ");

  $("answer").textContent = data.answer || "";
  $("answer").className = "answer" + (data.abstained ? " abstained" : "");

  // The grade decision is the abstention gate made visible — the whole point of
  // the "not in the data" demo beat.
  const why = $("grade-why");
  why.innerHTML = "";
  if (trace.grade_reason) {
    why.appendChild(el("b", null, data.abstained ? "Why it declined: " : "Evidence check: "));
    why.appendChild(document.createTextNode(trace.grade_reason));
  }

  showPanels(data, trace);
}

async function showPanels(data, trace) {
  // The router extracts question entities from capitalised words, so a question
  // written in lower case ("which team is alex on now?") resolves nothing and
  // every graph panel would stay hidden. Retry here against the alias index for
  // *display only* — the answer above is untouched by this.
  let ids = trace.entity_ids || [];
  if (!ids.length) ids = await resolveFromQuestion(data.question || $("question").value);

  renderConflicts(trace.conflicts || [], ids);
  renderEntities(ids);
  renderDocs(data.document_ids || [], trace.retrieved_doc_ids || []);
  renderGraph(ids);
}

const QUESTION_STOP = new Set([
  "which", "what", "who", "whom", "whose", "when", "where", "why", "how",
  "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
  "was", "were", "be", "been", "did", "does", "do", "with", "that", "this",
  "it", "at", "by", "from", "as", "we", "our", "us", "now", "team", "currently",
]);

async function resolveFromQuestion(question) {
  const words = (question || "")
    .split(/[^A-Za-z0-9_@.\-]+/)
    .filter((w) => w.length > 2 && !QUESTION_STOP.has(w.toLowerCase()))
    .slice(0, 6);

  const found = [];
  for (const w of words) {
    try {
      const r = await (await fetch(`/api/resolve?name=${encodeURIComponent(w)}`)).json();
      for (const e of r.entities || []) {
        if (!found.includes(e.canonical_id)) found.push(e.canonical_id);
      }
    } catch { /* ignore */ }
    if (found.length >= 3) break;
  }
  return found;
}

/* ------------------------------------------------------------------ */
/* conflicts — both sides, dated and sourced                           */
/* ------------------------------------------------------------------ */
async function renderConflicts(conflicts, entityIds) {
  const panel = $("panel-conflict");
  const box = $("conflicts");
  box.innerHTML = "";

  // The router only fills trace.conflicts on the conflict route, but a
  // superseded fact is worth showing whenever one exists on a resolved entity.
  let pairs = [];
  for (const cid of entityIds.slice(0, 3)) {
    try {
      const f = await (await fetch(`/api/facts/${encodeURIComponent(cid)}`)).json();
      if (!f.superseded?.length) continue;
      for (const old of f.superseded) {
        const current = (f.current || []).find((c) => c.rel_type === old.rel_type);
        pairs.push({ old, current, names: f.names || {} });
      }
    } catch { /* ignore */ }
  }

  if (!pairs.length) { panel.hidden = true; return; }
  panel.hidden = false;

  for (const { old, current, names } of pairs.slice(0, 4)) {
    const nameOf = (id) => names[id] || id;
    const row = el("div", "conflict");

    const now = el("div", "side current");
    now.appendChild(el("div", "tag", "Current"));
    const nowFact = el("div", "fact");
    if (current) {
      nowFact.textContent = `${nameOf(current.src)} ${prettyRel(current.rel_type)} ${nameOf(current.dst)}`;
    } else {
      nowFact.textContent = "no replacement recorded";
    }
    now.appendChild(nowFact);
    now.appendChild(el("div", "when", current
      ? `since ${fmtDate(current.valid_from || current.stated_at) || "unknown"}`
      : ""));
    if (current) now.appendChild(provenance(current));

    const then = el("div", "side old");
    then.appendChild(el("div", "tag", "Superseded — kept, not deleted"));
    const oldFact = el("div", "fact");
    const s = el("s", null, `${nameOf(old.src)} ${prettyRel(old.rel_type)} ${nameOf(old.dst)}`);
    oldFact.appendChild(s);
    then.appendChild(oldFact);
    then.appendChild(el("div", "when", `until ${fmtDate(old.valid_to) || "unknown"}`));
    then.appendChild(provenance(old));

    row.appendChild(then);
    row.appendChild(el("div", "arrow", "→"));
    row.appendChild(now);
    box.appendChild(row);
  }
}

function provenance(edge) {
  const wrap = el("div", "prov");
  const src = el("span", "src", edge.source_type || "?");
  src.dataset.s = edge.source_type || "";
  wrap.appendChild(src);
  (edge.source_doc_ids || []).slice(0, 3).forEach((id) => {
    const chip = el("span", "docid", id.slice(0, 18) + "…");
    chip.style.cursor = "pointer";
    chip.onclick = () => showDoc(id);
    wrap.appendChild(chip);
  });
  return wrap;
}

/* ------------------------------------------------------------------ */
/* entities — the alias merge                                          */
/* ------------------------------------------------------------------ */
async function renderEntities(entityIds) {
  const panel = $("panel-entity");
  const box = $("entities");
  box.innerHTML = "";
  if (!entityIds.length) { panel.hidden = true; return; }

  let shown = 0;
  for (const cid of entityIds.slice(0, 5)) {
    let e;
    try { e = await (await fetch(`/entity/${encodeURIComponent(cid)}`)).json(); }
    catch { continue; }
    if (!e || e.detail) continue;

    const node = el("div", "entity");
    const head = el("div", "entity-head");
    head.appendChild(el("span", "entity-name", e.canonical_name));
    head.appendChild(el("span", "pill ghost", e.entity_type));
    node.appendChild(head);

    const forms = el("div", "forms");
    (e.surface_forms || []).forEach((f) => {
      const chip = el("span", "form-chip" + (f === e.canonical_name ? " canon" : ""), f);
      forms.appendChild(chip);
    });
    node.appendChild(forms);

    const n = (e.surface_forms || []).length;
    if (n > 1) {
      node.appendChild(el("div", "merged-note",
        `${n} surface forms across ${(e.source_types || []).join(", ") || "the corpus"} `
        + `resolved to this one node.`));
    }
    box.appendChild(node);
    shown++;
  }
  panel.hidden = shown === 0;
}

/* ------------------------------------------------------------------ */
/* documents                                                           */
/* ------------------------------------------------------------------ */
async function renderDocs(cited, retrieved) {
  const panel = $("panel-docs");
  const box = $("docs");
  box.innerHTML = "";

  const ids = cited.length ? cited : retrieved.slice(0, 6);
  if (!ids.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("docs-hint").textContent = cited.length
    ? "cited in the answer above"
    : "retrieved, but not cited — the answer did not rely on them";

  let docs = [];
  try {
    const r = await (await fetch(`/api/docs?ids=${encodeURIComponent(ids.join(","))}`)).json();
    docs = r.docs || [];
  } catch { return; }

  docs.forEach((d) => {
    const card = el("div", "doc");
    card.onclick = () => showDoc(d.doc_id);
    const head = el("div", "doc-head");
    const src = el("span", "src", d.source_type);
    src.dataset.s = d.source_type;
    head.appendChild(src);
    head.appendChild(el("span", "doc-title", d.title || d.doc_id));
    card.appendChild(head);
    card.appendChild(el("div", "doc-snip", (d.body || "").slice(0, 190) + "…"));
    box.appendChild(card);
  });
}

async function showDoc(docId) {
  const body = $("modal-body");
  body.innerHTML = "";
  body.appendChild(el("p", "docid", "loading…"));
  $("modal").classList.remove("hidden");
  try {
    const d = await (await fetch(`/doc/${encodeURIComponent(docId)}`)).json();
    body.innerHTML = "";
    const head = el("div", "doc-head");
    const src = el("span", "src", d.source_type);
    src.dataset.s = d.source_type;
    head.appendChild(src);
    head.appendChild(el("span", "docid", d.doc_id));
    body.appendChild(head);
    body.appendChild(el("h3", null, d.title || "(untitled)"));
    if (d.timestamp) body.appendChild(el("div", "when", fmtDate(d.timestamp)));
    body.appendChild(el("pre", null, d.body || ""));
  } catch (e) {
    body.innerHTML = "";
    body.appendChild(el("p", null, `Could not load ${docId}: ${e.message}`));
  }
}

/* ------------------------------------------------------------------ */
/* graph                                                               */
/* ------------------------------------------------------------------ */
const TYPE_COLOR = {
  person: "#4c9aff", bot: "#8b949e", team: "#a371f7", role: "#f778ba",
  organization: "#d29922", channel: "#3fb950", document: "#58a6ff",
  ticket: "#ff9e64", pull_request: "#7ee787", meeting: "#79c0ff",
  project: "#ffa657", alias: "#6b7a8c",
};

async function renderGraph(entityIds) {
  const panel = $("panel-graph");
  if (!entityIds.length) { panel.hidden = true; return; }

  let data;
  try {
    data = await (await fetch(
      `/subgraph?ids=${encodeURIComponent(entityIds.slice(0, 3).join(","))}&max_len=2`)).json();
  } catch { panel.hidden = true; return; }

  if (!data.nodes?.length) { panel.hidden = true; return; }
  panel.hidden = false;

  const elements = [
    ...data.nodes.map((n) => ({
      data: {
        id: n.canonical_id,
        label: n.canonical_name || n.canonical_id,
        type: n.entity_type,
        seed: n.seed ? 1 : 0,
      },
    })),
    ...data.edges
      .filter((e) => e.src && e.dst)
      .map((e) => ({
        data: {
          id: e.edge_id || `${e.src}-${e.rel_type}-${e.dst}`,
          source: e.src, target: e.dst,
          label: prettyRel(e.rel_type),
          current: e.is_current ? 1 : 0,
        },
      })),
  ];

  // Drop edges whose endpoints were not returned, or cytoscape throws.
  const ids = new Set(data.nodes.map((n) => n.canonical_id));
  const safe = elements.filter((x) => !x.data.source
    || (ids.has(x.data.source) && ids.has(x.data.target)));

  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("cy"),
    elements: safe,
    style: [
      { selector: "node", style: {
        "background-color": (n) => TYPE_COLOR[n.data("type")] || "#8b949e",
        label: "data(label)", color: "#e6edf3",
        "font-size": 11, "text-valign": "bottom", "text-margin-y": 5,
        width: (n) => (n.data("seed") ? 30 : 18),
        height: (n) => (n.data("seed") ? 30 : 18),
        "border-width": (n) => (n.data("seed") ? 3 : 0),
        "border-color": "#e6edf3",
        "text-background-color": "#0f151d", "text-background-opacity": .75,
        "text-background-padding": 2,
      }},
      { selector: "edge", style: {
        width: 1.5,
        // A superseded fact is drawn as a red dashed line — the contradiction
        // is visible in the picture, not only in the text panel.
        "line-color": (e) => (e.data("current") ? "#2a3441" : "#f85149"),
        "line-style": (e) => (e.data("current") ? "solid" : "dashed"),
        "target-arrow-color": (e) => (e.data("current") ? "#2a3441" : "#f85149"),
        "target-arrow-shape": "triangle", "curve-style": "bezier",
        "arrow-scale": .8,
        label: "data(label)", "font-size": 8.5, color: "#6b7a8c",
        "text-rotation": "autorotate",
        "text-background-color": "#0f151d", "text-background-opacity": .85,
        "text-background-padding": 2,
      }},
    ],
    layout: { name: "cose", animate: false, padding: 30, nodeRepulsion: 9000,
              idealEdgeLength: 90 },
  });

  cy.on("tap", "node", (evt) => ask(`Who or what is ${evt.target.data("label")}?`));

  const types = [...new Set(data.nodes.map((n) => n.entity_type))];
  const legend = $("legend");
  legend.innerHTML = "";
  types.forEach((t) => {
    const s = el("span");
    const dot = el("i");
    dot.style.background = TYPE_COLOR[t] || "#8b949e";
    s.appendChild(dot);
    s.appendChild(document.createTextNode(t));
    legend.appendChild(s);
  });
  if (data.edges.some((e) => !e.is_current)) {
    const s = el("span");
    s.appendChild(el("span", "dash"));
    s.appendChild(document.createTextNode("superseded fact"));
    legend.appendChild(s);
  }
}

/* ------------------------------------------------------------------ */
/* helpers + wiring                                                    */
/* ------------------------------------------------------------------ */
function prettyRel(rel) {
  return (rel || "").toLowerCase().replace(/_/g, " ");
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("question").value.trim();
  if (q) ask(q);
});

$("examples").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-q]");
  if (btn) ask(btn.dataset.q);
});

$("modal-close").onclick = () => $("modal").classList.add("hidden");
$("modal").addEventListener("click", (e) => {
  if (e.target === $("modal")) $("modal").classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("modal").classList.add("hidden");
});

loadStats();
