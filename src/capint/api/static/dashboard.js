// Capital Intelligence OS — read-only dashboard.
// Everything renders from real GET requests to this same instance's own
// /api/v1/* endpoints, fetched client-side (same-origin, no new
// server-side logic). No company-scoped radar endpoints exist server-side,
// so radar lists are fetched once at top_n=500 (the API's max) and
// filtered/looked-up client-side for the company detail view.

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, html) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (html !== undefined) n.innerHTML = html;
  return n;
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return r.json();
}

function money(n) {
  const v = Number(n);
  if (Number.isNaN(v)) return String(n);
  return "$" + v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function scoreTier(s) {
  if (s === null || s === undefined) return "lo";
  if (s >= 70) return "hi";
  if (s >= 40) return "mid";
  return "lo";
}

function empty(msg) {
  return `<div class="empty">${msg}</div>`;
}

function errBox(e) {
  return `<div class="err">${e.message}</div>`;
}

function companyLink(id, name) {
  return `<a href="#/company/${id}">${name}</a>`;
}

// ---------------------------------------------------------------------
// Data cache — one fetch pass per page load, reused across views.
// ---------------------------------------------------------------------
const DATA = {
  companies: null, insider: null, institutional: null, shortInterest: null,
  convergence: null, alerts: null, alertRules: null, relationships: null,
  news: null, vix: null,
};

async function loadAll() {
  const [companies, insider, institutional, shortInterest, convergence, alerts, alertRules, relationships, news, vix] =
    await Promise.allSettled([
      getJSON("/api/v1/companies"),
      getJSON("/api/v1/radar/insider?top_n=500"),
      getJSON("/api/v1/radar/institutional?top_n=500"),
      getJSON("/api/v1/radar/short-interest?top_n=500"),
      getJSON("/api/v1/radar/convergence?top_n=500"),
      getJSON("/api/v1/alerts"),
      getJSON("/api/v1/alert-rules"),
      getJSON("/api/v1/relationships/interlocking-directorates"),
      getJSON("/api/v1/news-sentiment"),
      getJSON("/api/v1/volatility-index?index_code=VIX"),
    ]);
  const val = (r) => (r.status === "fulfilled" ? r.value : []);
  DATA.companies = val(companies);
  DATA.insider = val(insider);
  DATA.institutional = val(institutional);
  DATA.shortInterest = val(shortInterest);
  DATA.convergence = val(convergence);
  DATA.alerts = val(alerts);
  DATA.alertRules = val(alertRules);
  DATA.relationships = val(relationships);
  DATA.news = val(news);
  DATA.vix = val(vix);
}

function companyById(id) {
  return (DATA.companies || []).find((c) => c.entity_id === id);
}

// ---------------------------------------------------------------------
// Chart primitives (hand-rolled SVG — no external chart library).
// ---------------------------------------------------------------------

/** Horizontal ranked bar list. items: [{id, name, score, extra}] */
function renderBarList(container, items, opts = {}) {
  const max = opts.max || Math.max(1, ...items.map((i) => i.score || 0));
  if (!items.length) { container.innerHTML = empty(opts.emptyMsg || "No data."); return; }
  const rows = items.slice(0, opts.limit || 8).map((it) => {
    const pct = Math.max(2, ((it.score || 0) / max) * 100);
    const tier = scoreTier(it.score);
    return `<div class="barrow" data-id="${it.id}">
        <div class="name" title="${it.name}">${it.name}</div>
        <div class="track"><div class="fill ${tier}" style="width:${pct}%"></div></div>
        <div class="val">${it.score ?? "—"}</div>
      </div>`;
  }).join("");
  container.innerHTML = rows;
  container.querySelectorAll(".barrow").forEach((row) => {
    row.addEventListener("click", () => { location.hash = "#/company/" + row.dataset.id; });
  });
}

/** Small component weight/value bars, used on the company detail page. */
function renderComponents(container, components) {
  if (!components || !components.length) { container.innerHTML = ""; return; }
  container.innerHTML = components.map((c) => {
    const v = c.value === null || c.value === undefined ? null : Number(c.value);
    const pct = v === null ? 0 : Math.max(0, Math.min(100, v));
    return `<div class="component-row">
        <div class="cn">${c.name.replace(/_/g, " ")}</div>
        <div class="track"><div class="fill" style="width:${pct}%"></div></div>
        <div class="cv">${v === null ? "—" : v.toFixed(0)}</div>
      </div>
      <div class="component-note">${c.explanation}</div>`;
  }).join("");
}

/** Semicircular gauge for a tone value roughly in [-5, 5]. */
function sentimentGaugeSVG(tone) {
  const clamped = Math.max(-5, Math.min(5, Number(tone)));
  const frac = (clamped + 5) / 10; // 0..1
  const angle = Math.PI * (1 - frac); // pi (left) .. 0 (right)
  const cx = 90, cy = 90, r = 70;
  const nx = cx + r * Math.cos(angle), ny = cy - r * Math.sin(angle);
  const color = tone > 0.5 ? "#3ecf8e" : tone < -0.5 ? "#f2596b" : "#e8b93f";
  const arc = (a0, a1, col) => {
    const x0 = cx + r * Math.cos(a0), y0 = cy - r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy - r * Math.sin(a1);
    return `<path d="M ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1}" stroke="${col}" stroke-width="12" fill="none" stroke-linecap="round"/>`;
  };
  return `<svg viewBox="0 0 180 110" style="width:180px;height:110px">
    ${arc(Math.PI, Math.PI * 2 / 3, "#2a3346")}
    ${arc(Math.PI * 2 / 3, Math.PI / 3, "#2a3346")}
    ${arc(Math.PI / 3, 0, "#2a3346")}
    <line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="${color}" stroke-width="3"/>
    <circle cx="${cx}" cy="${cy}" r="4" fill="${color}"/>
    <text x="10" y="105" font-size="9">-5</text>
    <text x="160" y="105" font-size="9">+5</text>
  </svg>`;
}

/** VIX line chart with light gridlines. */
function vixSVG(rows) {
  const recent = rows.slice(0, 90).slice().reverse();
  const closes = recent.map((r) => Number(r.close));
  const w = 1100, h = 180, padL = 40, padR = 16, padT = 16, padB = 26;
  const min = Math.min(...closes), max = Math.max(...closes);
  const x = (i) => padL + (i / (closes.length - 1)) * (w - padL - padR);
  const y = (v) => h - padB - ((v - min) / (max - min || 1)) * (h - padT - padB);
  const points = closes.map((v, i) => x(i) + "," + y(v).toFixed(1)).join(" ");
  const gridY = [min, min + (max - min) / 2, max];
  const grid = gridY.map((v) => `<line x1="${padL}" x2="${w - padR}" y1="${y(v)}" y2="${y(v)}" stroke="#1a2030"/>
      <text x="4" y="${y(v) + 3}">${v.toFixed(1)}</text>`).join("");
  const first = recent[0]?.trade_date, last = recent[recent.length - 1]?.trade_date;
  const areaId = "vixarea";
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:190px">
    <defs><linearGradient id="${areaId}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#5b9dff" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#5b9dff" stop-opacity="0"/>
    </linearGradient></defs>
    ${grid}
    <polygon points="${padL},${h - padB} ${points} ${w - padR},${h - padB}" fill="url(#${areaId})"/>
    <polyline points="${points}" fill="none" stroke="#5b9dff" stroke-width="2"/>
    <text x="${padL}" y="${h - 6}">${first || ""}</text>
    <text x="${w - padR - 70}" y="${h - 6}">${last || ""}</text>
  </svg>`;
}

/** Donut chart for convergence label distribution. */
function donutSVG(counts) {
  const colors = { INSIDER_ONLY: "#5b9dff", INSTITUTIONAL_ONLY: "#e8b93f", SHORT_INTEREST_ONLY: "#8891a5",
    INSIDER_AND_INSTITUTIONAL_ACCUMULATING: "#3ecf8e" };
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  const r = 46, cx = 55, cy = 55, circumference = 2 * Math.PI * r;
  let offset = 0;
  const segs = Object.entries(counts).filter(([, n]) => n > 0).map(([label, n]) => {
    const frac = n / total;
    const dash = frac * circumference;
    const seg = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[label] || "#5b6376"}"
      stroke-width="16" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${-offset}"
      transform="rotate(-90 ${cx} ${cy})"/>`;
    offset += dash;
    return seg;
  }).join("");
  const legend = Object.entries(counts).filter(([, n]) => n > 0).map(([label, n]) =>
    `<div class="row"><span class="sw" style="background:${colors[label] || "#5b6376"}"></span>${label.replace(/_/g, " ")} — ${n}</div>`
  ).join("");
  return `<div class="donut-wrap">
    <svg viewBox="0 0 110 110" style="width:110px;height:110px">${segs}
      <text x="55" y="59" text-anchor="middle" font-size="16" fill="#e8eaf1">${total}</text>
    </svg>
    <div class="donut-legend">${legend}</div>
  </div>`;
}

// ---------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------

function setCrumbs(html) { $("#crumbs").innerHTML = html; }

function renderOverview() {
  setCrumbs(`<span class="cur">Overview</span>`);
  const app = $("#app");
  app.innerHTML = `
    <div class="kpis" id="kpis">
      <div class="kpi"><div class="n">${DATA.companies.length}</div><div class="l">Companies</div></div>
      <div class="kpi"><div class="n">${DATA.alerts.length}</div><div class="l">Alerts fired</div></div>
      <div class="kpi"><div class="n">${DATA.relationships.length}</div><div class="l">Interlocks</div></div>
      <div class="kpi"><div class="n">${DATA.vix.length ? Number(DATA.vix[0].close).toFixed(2) : "—"}</div><div class="l">VIX latest close</div></div>
    </div>
    <div class="grid">
      <div class="panel">
        <h2>Insider Radar <span class="badge">composite score</span></h2>
        <div id="c-insider"></div>
      </div>
      <div class="panel">
        <h2>Institutional Radar <span class="badge">composite score</span></h2>
        <div id="c-institutional"></div>
      </div>
      <div class="panel">
        <h2>Short Interest Radar <span class="badge">acceleration</span></h2>
        <div id="c-shortinterest"></div>
      </div>
      <div class="panel">
        <h2>Convergence Labels <span class="badge">${DATA.convergence.length} companies</span></h2>
        <div id="c-donut"></div>
      </div>
      <div class="panel">
        <h2>Alerts <span class="badge">fired rules</span></h2>
        <div id="c-alerts"></div>
      </div>
      <div class="panel">
        <h2>Relationships <span class="badge">interlocking directorates</span></h2>
        <div id="c-rel"></div>
      </div>
      <div class="panel">
        <h2>News Sentiment <span class="badge">GDELT tone</span></h2>
        <div id="c-news"></div>
      </div>
      <div class="panel wide">
        <h2>VIX <span class="badge">Cboe, daily close, real history</span></h2>
        <div id="c-vix"></div>
      </div>
    </div>`;

  renderBarList($("#c-insider"), DATA.insider.map((r) => ({
    id: r.company_entity_id, name: r.company_name, score: r.composite_score,
  })), { emptyMsg: "No companies currently clear this radar's screening threshold." });

  renderBarList($("#c-institutional"), DATA.institutional.map((r) => ({
    id: r.company_entity_id, name: r.company_name, score: r.composite_score,
  })), { emptyMsg: "No companies currently clear this radar's screening threshold." });

  renderBarList($("#c-shortinterest"), DATA.shortInterest.map((r) => ({
    id: r.company_entity_id, name: r.company_name, score: r.composite_score,
  })), { max: 100, emptyMsg: "No companies currently clear this radar's screening threshold." });

  const counts = {};
  for (const c of DATA.convergence) counts[c.label] = (counts[c.label] || 0) + 1;
  $("#c-donut").innerHTML = Object.keys(counts).length ? donutSVG(counts) : empty("No convergence entries yet.");

  if (!DATA.alerts.length) {
    $("#c-alerts").innerHTML = empty("No alert rules have fired yet.");
  } else {
    const ruleName = {}; for (const r of DATA.alertRules) ruleName[r.id] = r.name;
    let html = '<table><thead><tr><th>Rule</th><th>Company</th><th>Date</th><th>Delivered</th></tr></thead><tbody>';
    for (const a of DATA.alerts.slice(0, 8)) {
      const c = companyById(a.company_entity_id);
      const delivered = a.delivery_attempted
        ? (a.delivery_succeeded ? '<span class="pill green">sent</span>' : '<span class="pill red">failed</span>')
        : '<span class="pill plain">no webhook</span>';
      html += `<tr class="clickable" data-id="${a.company_entity_id}">
        <td>${ruleName[a.rule_id] || a.rule_id}</td>
        <td>${c ? c.canonical_name : a.company_entity_id}</td>
        <td>${a.alert_date}</td><td>${delivered}</td></tr>`;
    }
    $("#c-alerts").innerHTML = html + "</tbody></table>";
    wireRowClicks($("#c-alerts"));
  }

  if (!DATA.relationships.length) {
    $("#c-rel").innerHTML = empty('None found in the data ingested so far — board interlocks are real but rare in a small sample.');
  } else {
    let html = '<table><thead><tr><th>Person</th><th>Company A</th><th>Company B</th></tr></thead><tbody>';
    for (const r of DATA.relationships.slice(0, 8)) {
      html += `<tr><td>${r.person_name}</td><td>${companyLink(r.company_a_entity_id, r.company_a_name)}</td><td>${companyLink(r.company_b_entity_id, r.company_b_name)}</td></tr>`;
    }
    $("#c-rel").innerHTML = html + "</tbody></table>";
  }

  if (!DATA.news.length) {
    $("#c-news").innerHTML = empty("No news-sentiment snapshots ingested yet.");
  } else {
    let html = '<table><thead><tr><th>Company</th><th>Query</th><th>Articles</th><th>Mean tone</th></tr></thead><tbody>';
    for (const r of DATA.news.slice(0, 8)) {
      const c = companyById(r.company_entity_id);
      const tone = Number(r.mean_tone);
      const cls = tone > 0 ? "green" : tone < 0 ? "red" : "plain";
      html += `<tr class="clickable" data-id="${r.company_entity_id}">
        <td>${c ? c.canonical_name : r.company_entity_id}</td><td>${r.query}</td><td>${r.article_count}</td>
        <td><span class="pill ${cls}">${tone > 0 ? "+" : ""}${tone}</span></td></tr>`;
    }
    $("#c-news").innerHTML = html + "</tbody></table>";
    wireRowClicks($("#c-news"));
  }

  $("#c-vix").innerHTML = DATA.vix.length ? vixSVG(DATA.vix) : empty("No VIX data ingested.");
}

function wireRowClicks(root) {
  root.querySelectorAll("tr.clickable").forEach((row) => {
    row.addEventListener("click", () => { location.hash = "#/company/" + row.dataset.id; });
  });
}

function radarEntryFor(list, key, companyId) {
  return (list || []).find((r) => r.company_entity_id === companyId);
}

function renderCompany(id) {
  const company = companyById(id);
  const app = $("#app");
  if (!company) {
    app.innerHTML = empty("Unknown company id — try navigating from the overview.");
    setCrumbs(`<a href="#/">Overview</a><span class="sep">/</span><span class="cur">Unknown</span>`);
    return;
  }
  setCrumbs(`<a href="#/">Overview</a><span class="sep">/</span><span class="cur">${company.canonical_name}</span>`);

  const insider = radarEntryFor(DATA.insider, "company_entity_id", id);
  const institutional = radarEntryFor(DATA.institutional, "company_entity_id", id);
  const shortInterest = radarEntryFor(DATA.shortInterest, "company_entity_id", id);
  const convergence = radarEntryFor(DATA.convergence, "company_entity_id", id);
  const alerts = DATA.alerts.filter((a) => a.company_entity_id === id);
  const rels = DATA.relationships.filter((r) => r.company_a_entity_id === id || r.company_b_entity_id === id);
  const news = DATA.news.filter((n) => n.company_entity_id === id);

  app.innerHTML = `
    <div class="company-header">
      <div>
        <h1>${company.canonical_name}</h1>
        <div class="company-meta">${company.entity_type}<span class="sep">·</span>${company.sector || "sector unknown"}<span class="sep">·</span>${company.country || "country unknown"}</div>
      </div>
    </div>
    <div class="grid">
      <div class="panel">
        <h2>Insider Radar</h2>
        <div id="d-insider"></div>
      </div>
      <div class="panel">
        <h2>Institutional Radar</h2>
        <div id="d-institutional"></div>
      </div>
      <div class="panel">
        <h2>Short Interest Radar</h2>
        <div id="d-shortinterest"></div>
      </div>
      <div class="panel">
        <h2>Convergence</h2>
        <div id="d-convergence"></div>
      </div>
      <div class="panel">
        <h2>Alerts <span class="badge">${alerts.length}</span></h2>
        <div id="d-alerts"></div>
      </div>
      <div class="panel">
        <h2>Relationships <span class="badge">${rels.length}</span></h2>
        <div id="d-rel"></div>
      </div>
      <div class="panel wide">
        <h2>News Sentiment <span class="badge">${news.length}</span></h2>
        <div id="d-news"></div>
      </div>
    </div>`;

  renderRadarDetail($("#d-insider"), insider, [
    { key: "distinct_insiders", label: "Distinct insiders" },
    { key: "window_total_dollar_value", label: "$ value", fmt: money },
  ], (r) => renderEvidence(r.evidence, [
    { key: "insider_name", label: "Insider" },
    { key: "transaction_date", label: "Date" },
    { key: "shares_transacted", label: "Shares" },
    { key: "price_per_share", label: "Price" },
    { key: "dollar_value", label: "$ value", fmt: money },
  ]));

  renderRadarDetail($("#d-institutional"), institutional, [
    { key: "distinct_accumulating_institutions", label: "Accumulating institutions" },
    { key: "window_total_dollar_value", label: "$ value", fmt: money },
  ], (r) => renderEvidence(r.evidence, [
    { key: "institution_name", label: "Institution" },
    { key: "period_of_report", label: "Period" },
    { key: "shares_held", label: "Shares held" },
    { key: "market_value_usd", label: "$ value", fmt: money },
  ]));

  renderRadarDetail($("#d-shortinterest"), shortInterest, [
    { key: "latest_change_percent", label: "Change %" },
    { key: "latest_days_to_cover", label: "Days to cover" },
  ], (r) => renderEvidence(r.evidence, [
    { key: "settlement_date", label: "Settlement" },
    { key: "current_short_position", label: "Short position" },
    { key: "change_percent", label: "Change %" },
    { key: "days_to_cover", label: "Days to cover" },
  ]));

  if (convergence) {
    $("#d-convergence").innerHTML = `<span class="pill">${convergence.label}</span><p class="component-note">${convergence.label_explanation}</p>`;
  } else {
    $("#d-convergence").innerHTML = empty("This company doesn't appear in the convergence result set.");
  }

  if (!alerts.length) {
    $("#d-alerts").innerHTML = empty("No alerts have fired for this company.");
  } else {
    const ruleName = {}; for (const r of DATA.alertRules) ruleName[r.id] = r.name;
    let html = '<table><thead><tr><th>Rule</th><th>Date</th><th>Summary</th></tr></thead><tbody>';
    for (const a of alerts) html += `<tr><td>${ruleName[a.rule_id] || a.rule_id}</td><td>${a.alert_date}</td><td>${a.signal_summary}</td></tr>`;
    $("#d-alerts").innerHTML = html + "</tbody></table>";
  }

  if (!rels.length) {
    $("#d-rel").innerHTML = empty("No interlocking directorates involving this company.");
  } else {
    let html = '<table><thead><tr><th>Person</th><th>Also serves at</th></tr></thead><tbody>';
    for (const r of rels) {
      const other = r.company_a_entity_id === id
        ? { id: r.company_b_entity_id, name: r.company_b_name }
        : { id: r.company_a_entity_id, name: r.company_a_name };
      html += `<tr><td>${r.person_name}</td><td>${companyLink(other.id, other.name)}</td></tr>`;
    }
    $("#d-rel").innerHTML = html + "</tbody></table>";
  }

  if (!news.length) {
    $("#d-news").innerHTML = empty("No news-sentiment snapshots for this company.");
  } else {
    $("#d-news").innerHTML = news.map((n) => `
      <div class="gauge-wrap">
        ${sentimentGaugeSVG(n.mean_tone)}
        <div class="gauge-num">Query <b>"${n.query}"</b> — ${n.article_count} articles over ${n.timespan}<br>
          mean tone <b>${Number(n.mean_tone) > 0 ? "+" : ""}${n.mean_tone}</b>, retrieved ${new Date(n.retrieved_at).toLocaleString()}</div>
      </div>`).join("<hr style='border-color:var(--border);margin:14px 0'>");
  }
}

function renderRadarDetail(container, entry, extraFields, evidenceRenderer) {
  if (!entry) {
    container.innerHTML = empty("Doesn't clear this radar's screening threshold.");
    return;
  }
  const tier = scoreTier(entry.composite_score);
  const extras = extraFields.map((f) => {
    const raw = entry[f.key];
    return `<span class="pill plain">${f.label}: ${f.fmt ? f.fmt(raw) : raw}</span>`;
  }).join(" ");
  container.innerHTML = `
    <div class="score-badge ${tier}">${entry.composite_score}</div>
    <div style="margin:8px 0">${extras}</div>
    <div class="components" id="rc-${container.id}"></div>
    <div id="re-${container.id}"></div>`;
  renderComponents($("#rc-" + container.id), entry.components);
  $("#re-" + container.id).innerHTML = evidenceRenderer(entry);
}

function renderEvidence(rows, cols) {
  if (!rows || !rows.length) return "";
  let html = '<table class="evidence-table"><thead><tr>' + cols.map((c) => `<th>${c.label}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    html += "<tr>" + cols.map((c) => `<td>${c.fmt ? c.fmt(r[c.key]) : (r[c.key] ?? "—")}</td>`).join("") + "</tr>";
  }
  return html + "</tbody></table>";
}

// ---------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------
function route() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/company\/(.+)$/);
  if (m) renderCompany(decodeURIComponent(m[1]));
  else renderOverview();
  closeSearch();
}

// ---------------------------------------------------------------------
// Company search — matches canonical_name (also sector/country/ticker-ish
// substrings) against DATA.companies, client-side only, no new endpoint.
// ---------------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function highlight(text, query) {
  const i = text.toLowerCase().indexOf(query.toLowerCase());
  if (i === -1) return escapeHtml(text);
  return escapeHtml(text.slice(0, i)) + "<mark>" + escapeHtml(text.slice(i, i + query.length)) + "</mark>" + escapeHtml(text.slice(i + query.length));
}

function searchCompanies(query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return (DATA.companies || [])
    .filter((c) => c.canonical_name.toLowerCase().includes(q) || (c.sector || "").toLowerCase().includes(q) || (c.country || "").toLowerCase().includes(q))
    .sort((a, b) => a.canonical_name.toLowerCase().indexOf(q) - b.canonical_name.toLowerCase().indexOf(q))
    .slice(0, 8);
}

let searchActiveIndex = -1;

function renderSearchResults(matches, query) {
  const box = $("#search-results");
  searchActiveIndex = -1;
  if (!query.trim()) { box.hidden = true; box.innerHTML = ""; return; }
  if (!matches.length) {
    box.innerHTML = '<div class="empty-row">No companies match "' + escapeHtml(query) + '".</div>';
    box.hidden = false;
    return;
  }
  box.innerHTML = matches.map((c, i) => `
    <div class="row" data-id="${c.entity_id}" data-idx="${i}">
      <span>${highlight(c.canonical_name, query)}</span>
      <span class="meta">${[c.sector, c.country].filter(Boolean).join(" · ") || c.entity_type}</span>
    </div>`).join("");
  box.hidden = false;
  box.querySelectorAll(".row").forEach((row) => {
    row.addEventListener("mousedown", (e) => { e.preventDefault(); goToCompany(row.dataset.id); });
  });
}

function goToCompany(id) {
  location.hash = "#/company/" + id;
  const input = $("#search");
  input.value = "";
  input.blur();
  closeSearch();
}

function closeSearch() {
  const box = $("#search-results");
  if (box) { box.hidden = true; box.innerHTML = ""; }
  searchActiveIndex = -1;
}

function setActiveRow(delta) {
  const rows = [...document.querySelectorAll("#search-results .row")];
  if (!rows.length) return;
  searchActiveIndex = (searchActiveIndex + delta + rows.length) % rows.length;
  rows.forEach((r, i) => r.classList.toggle("active", i === searchActiveIndex));
  rows[searchActiveIndex].scrollIntoView({ block: "nearest" });
}

function initSearch() {
  const input = $("#search");
  input.addEventListener("input", () => {
    renderSearchResults(searchCompanies(input.value), input.value);
  });
  input.addEventListener("keydown", (e) => {
    const rows = [...document.querySelectorAll("#search-results .row")];
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveRow(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveRow(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const target = searchActiveIndex >= 0 ? rows[searchActiveIndex] : rows[0];
      if (target) goToCompany(target.dataset.id);
    } else if (e.key === "Escape") {
      closeSearch();
      input.blur();
    }
  });
  input.addEventListener("focus", () => { if (input.value.trim()) renderSearchResults(searchCompanies(input.value), input.value); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-wrap")) closeSearch();
  });
}

async function main() {
  $("#ts").textContent = new Date().toLocaleString();
  try {
    await loadAll();
  } catch (e) {
    $("#app").innerHTML = errBox(e);
    return;
  }
  initSearch();
  window.addEventListener("hashchange", route);
  route();
}

main();
