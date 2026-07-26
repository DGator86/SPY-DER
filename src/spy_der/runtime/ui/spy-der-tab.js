/* SPY-DER dashboard tab — one implementation, two mounts.
 *
 * Mount A: 0DTE's Vercel dashboard, as a tab beside Legacy / V2 / V3.
 * Mount B: SPY-DER's own read-only dashboard API at `/ui`, which is what keeps
 *          working after 0DTE is retired.
 *
 * Both mounts load this same file, so there is no second copy to drift. The
 * 0DTE side needs no JavaScript edit: drop a container with `data-spy-der-tab`
 * on it and this module finds it. See
 * `integrations/zerodte/spy_der_tab/README.md`.
 *
 * Constraints this file is written to:
 *
 *   - No dependencies, no build step. It is loaded as a plain ES module.
 *   - Read-only. It issues GETs and renders. There is no code path here that
 *     can submit, size, approve or promote anything — the execution guard is
 *     the only route to a trade and a browser must not be a second one.
 *   - No `innerHTML` with server data. Every value goes through `text()`, so a
 *     rationale or reason code cannot inject markup into 0DTE's page.
 *   - Panels fail independently. One unreadable artifact greys out its own
 *     panel and says why; it never blanks the tab. A dashboard that shows
 *     nothing because one file is missing is worse than one showing "unknown",
 *     which is the same principle `runtime/system_status.py` is built on.
 */

const DEFAULT_ENDPOINTS = {
  system: "/v1/system",
  state: "/v1/state",
  dojo: "/v1/dojo/latest",
  validation: "/v1/validation/latest",
  attribution: "/v1/attribution/latest",
};

const DEFAULT_REFRESH_MS = 30000;

/* -------------------------------------------------------------------------- */
/* DOM helpers                                                                */
/* -------------------------------------------------------------------------- */

function el(tag, className, textContent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent !== undefined && textContent !== null) {
    node.textContent = String(textContent);
  }
  return node;
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function panel(title, wide = false) {
  const box = el("section", "spyder-tab__panel" + (wide ? " spyder-tab__panel--wide" : ""));
  box.appendChild(el("h3", null, title));
  return box;
}

function kv(pairs) {
  const list = el("dl", "spyder-tab__kv");
  for (const [key, value] of pairs) {
    list.appendChild(el("dt", null, key));
    list.appendChild(el("dd", null, text(value)));
  }
  return list;
}

function tags(values, tone) {
  const list = el("ul", "spyder-tab__tags");
  for (const value of values) {
    const item = el("li", null, value);
    const resolved = typeof tone === "function" ? tone(value) : tone;
    if (resolved) item.dataset.tone = resolved;
    list.appendChild(item);
  }
  return list;
}

function note(message, bad = false) {
  return el("p", "spyder-tab__note" + (bad ? " spyder-tab__note--bad" : ""), message);
}

/* -------------------------------------------------------------------------- */
/* Formatting                                                                 */
/* -------------------------------------------------------------------------- */

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function money(value) {
  const parsed = num(value);
  if (parsed === null) return "—";
  const sign = parsed < 0 ? "-" : "";
  const body = Math.abs(parsed).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}$${body}`;
}

function pct(value) {
  const parsed = num(value);
  return parsed === null ? "—" : `${(parsed * 100).toFixed(1)}%`;
}

function ageLabel(iso) {
  const when = Date.parse(iso);
  if (!Number.isFinite(when)) return "—";
  const seconds = Math.max(0, (Date.now() - when) / 1000);
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function signClass(prefix, value) {
  const parsed = num(value);
  if (parsed === null || parsed === 0) return "";
  return parsed > 0 ? `${prefix}--pos` : `${prefix}--neg`;
}

/* -------------------------------------------------------------------------- */
/* Normalization                                                              */
/* -------------------------------------------------------------------------- */

/* `live_state.json` carries one of two published shapes: a `spyder.dashboard.v1`
 * DashboardPacket written by the decision service, or a `spy_der.parallel.v1`
 * heartbeat from the VPS runner whose decision fields live under `parallel`.
 * Both are legitimate live states, so the tab reads either rather than
 * rendering "unknown" against a perfectly good heartbeat. */
function normalizeState(raw) {
  if (!raw || typeof raw !== "object") return null;
  const parallel = raw.parallel && typeof raw.parallel === "object" ? raw.parallel : {};
  const pick = (key) => (raw[key] !== undefined && raw[key] !== null ? raw[key] : parallel[key]);
  return {
    schema: text(raw.schema_version, "unknown"),
    action: text(pick("action"), "UNKNOWN"),
    mode: pick("mode"),
    symbol: text(raw.symbol, "SPY"),
    snapshotId: pick("snapshot_id"),
    candidateId: pick("candidate_id"),
    structure: pick("structure"),
    direction: pick("direction"),
    confidence: num(pick("confidence")),
    uncertainty: num(pick("uncertainty")),
    sizeScalar: num(raw.size_scalar !== undefined ? raw.size_scalar : parallel.size_cap),
    reasonCodes: Array.isArray(pick("reason_codes")) ? pick("reason_codes") : [],
    rationale: pick("rationale") || parallel.note,
    traderModel: pick("trader_model"),
    reviewerModel: pick("reviewer_model"),
    provider: pick("provider") || pick("source"),
    available: raw.available !== false,
    generatedAt: pick("generated_at"),
    liveExecutionEnabled: raw.live_execution_enabled === true,
    dojo: raw.dojo && typeof raw.dojo === "object" ? raw.dojo : null,
    openPositions: Array.isArray(raw.open_positions) ? raw.open_positions : [],
  };
}

const ACTION_TONE = {
  TRADE: "ok",
  SELECT_CANDIDATE: "ok",
  APPROVE: "ok",
  RESIZE: "warn",
  NO_EDGE: "",
  IDLE: "",
  ABSTAIN: "",
  VETO: "bad",
  BLOCKED: "bad",
  UNAVAILABLE: "bad",
  UNKNOWN: "bad",
};

/* -------------------------------------------------------------------------- */
/* Panels                                                                     */
/* -------------------------------------------------------------------------- */

/* The decision hierarchy is what distinguishes SPY-DER from a chat wrapper, so
 * it gets rendered as the first thing on the tab rather than buried: the AI
 * chooses among candidates deterministic code already ruled eligible, and the
 * guard re-derives every limit afterwards. Stages the published packet does not
 * carry yet are drawn as "not published" rather than faked — an invented stage
 * count would misrepresent exactly the property the panel exists to show. */
function renderChain(state) {
  const box = panel("Decision chain", true);
  const chain = el("div", "spyder-tab__chain");

  const stage = (name, value, detail, tone) => {
    const card = el("div", "spyder-tab__stage" + (tone ? ` spyder-tab__stage--${tone}` : ""));
    card.appendChild(el("div", "spyder-tab__stage-name", name));
    card.appendChild(el("div", "spyder-tab__stage-value", text(value)));
    if (detail) card.appendChild(el("div", "spyder-tab__stage-detail", detail));
    return card;
  };

  const vetoed = state.action === "VETO" || state.action === "BLOCKED";
  chain.appendChild(
    stage("Candidates", state.candidateId ? "eligible set" : "not published", "geometry + payoff proofs", "ok")
  );
  chain.appendChild(
    stage("Deterministic risk", vetoed ? "VETO" : "passed", "risk.firewall", vetoed ? "bad" : "ok")
  );
  chain.appendChild(
    stage(
      "Decision authority",
      state.action,
      text(state.provider, "provider unknown"),
      "authority"
    )
  );
  chain.appendChild(
    stage(
      "Execution guard",
      state.sizeScalar !== null ? `size ×${state.sizeScalar.toFixed(2)}` : "not published",
      "limits re-derived, can only shrink",
      state.sizeScalar !== null && state.sizeScalar < 1 ? "warn" : "ok"
    )
  );
  chain.appendChild(
    stage(
      "Executor",
      state.liveExecutionEnabled ? "LIVE" : "shadow / paper",
      state.liveExecutionEnabled ? "live routing enabled" : "live routing disabled",
      state.liveExecutionEnabled ? "warn" : "ok"
    )
  );

  box.appendChild(chain);
  box.appendChild(
    el(
      "p",
      "spyder-tab__chain-caption",
      "The AI may only choose among candidates deterministic code already ruled " +
        "eligible. The guard re-derives every limit from the candidate set and " +
        "account state, so a decision can only shrink exposure — never assert " +
        "its way past a limit, and never invent a candidate."
    )
  );
  return box;
}

function renderDecision(state) {
  const box = panel("Current decision");

  const meter = (label, value, tone) => {
    const wrap = el("div", "spyder-tab__meter");
    const head = el("div", "spyder-tab__meter-head");
    head.appendChild(el("span", null, label));
    head.appendChild(el("span", null, pct(value)));
    wrap.appendChild(head);
    const track = el("div", "spyder-tab__meter-track");
    const fill = el("div", "spyder-tab__meter-fill" + (tone ? ` spyder-tab__meter-fill--${tone}` : ""));
    const width = value === null ? 0 : Math.max(0, Math.min(1, value)) * 100;
    fill.style.width = `${width}%`;
    track.appendChild(fill);
    wrap.appendChild(track);
    return wrap;
  };

  box.appendChild(meter("Confidence", state.confidence, "ok"));
  box.appendChild(meter("Uncertainty", state.uncertainty, "warn"));
  box.appendChild(
    kv([
      ["Structure", state.structure],
      ["Direction", state.direction],
      ["Candidate", state.candidateId],
      ["Snapshot", state.snapshotId],
      ["Mode", state.mode],
      ["Trader", state.traderModel],
      ["Reviewer", state.reviewerModel],
    ])
  );

  if (state.reasonCodes.length) {
    box.appendChild(el("h3", null, "Reason codes"));
    box.appendChild(tags(state.reasonCodes, (code) => (/veto|block|reject/i.test(code) ? "bad" : null)));
  }
  if (state.rationale) {
    box.appendChild(el("p", "spyder-tab__rationale", state.rationale));
  }
  return box;
}

/* The point of the shadow account: two books scored separately, and the gap
 * between them decomposed. `verdict` names which side is costing money, which
 * is the question a single P&L number cannot answer. */
function renderAttribution(body) {
  const box = panel("Shadow account — model vs execution", true);
  if (!body) {
    box.appendChild(note("No attribution report yet. Run settlement to produce one."));
    return box;
  }

  const VERDICTS = {
    healthy: ["ok", "Model profitable and executed faithfully."],
    execution_drag: ["warn", "The model made money; execution gave some back."],
    model_weak: ["bad", "Executed faithfully — the forecast is the problem."],
    model_weak_and_execution_drag: ["bad", "Both sides are losing money."],
    no_data: ["", "Not enough settled trades to judge."],
  };
  const [tone, blurb] = VERDICTS[body.verdict] || ["", ""];

  const verdict = el("p", "spyder-tab__pill" + (tone ? ` spyder-tab__pill--${tone}` : ""));
  verdict.textContent = text(body.verdict).replace(/_/g, " ");
  box.appendChild(verdict);
  if (blurb) box.appendChild(note(blurb));

  const books = el("div", "spyder-tab__books");
  const book = (name, value, sub) => {
    const card = el("div", "spyder-tab__book");
    card.appendChild(el("div", "spyder-tab__book-name", name));
    card.appendChild(
      el("div", "spyder-tab__book-value " + signClass("spyder-tab__book-value", value), money(value))
    );
    if (sub) card.appendChild(el("div", "spyder-tab__book-sub", sub));
    return card;
  };
  books.appendChild(
    book("Model book", body.model_pnl, `win rate ${pct(body.model_win_rate)} · n=${text(body.n_planned, "0")}`)
  );
  books.appendChild(
    book("Actual book", body.actual_pnl, `win rate ${pct(body.actual_win_rate)} · n=${text(body.n_taken, "0")}`)
  );
  books.appendChild(book("Gap", body.gap, "actual − model"));
  box.appendChild(books);

  const components = body.components && typeof body.components === "object" ? body.components : {};
  const entries = Object.entries(components);
  if (entries.length) {
    const magnitudes = entries.map(([, v]) => Math.abs(num(v) || 0));
    const scale = Math.max(...magnitudes, 1);
    const waterfall = el("div", "spyder-tab__waterfall");
    for (const [name, value] of entries) {
      const parsed = num(value) || 0;
      const row = el("div", "spyder-tab__bar-row");
      row.appendChild(el("div", "spyder-tab__bar-label", name));

      const track = el("div", "spyder-tab__bar-track");
      const negHalf = el("div", "spyder-tab__bar-half spyder-tab__bar-half--neg");
      const posHalf = el("div", "spyder-tab__bar-half");
      const fill = el("div", "spyder-tab__bar-fill " + (parsed < 0 ? "spyder-tab__bar-fill--neg" : "spyder-tab__bar-fill--pos"));
      fill.style.width = `${(Math.abs(parsed) / scale) * 100}%`;
      (parsed < 0 ? negHalf : posHalf).appendChild(fill);
      track.appendChild(negHalf);
      track.appendChild(posHalf);
      row.appendChild(track);

      row.appendChild(
        el("div", "spyder-tab__bar-value " + signClass("spyder-tab__bar-value", parsed), money(parsed))
      );
      waterfall.appendChild(row);
    }
    box.appendChild(el("h3", null, "Gap decomposition"));
    box.appendChild(waterfall);
    box.appendChild(
      note("Components sum to the gap exactly — there is no residual bucket.")
    );
  }

  const flags = body.flag_counts && typeof body.flag_counts === "object" ? body.flag_counts : {};
  const flagNames = Object.keys(flags);
  if (flagNames.length) {
    box.appendChild(el("h3", null, "Behavioural flags"));
    box.appendChild(
      tags(
        flagNames.map((name) => `${name.replace(/_/g, " ")} ×${flags[name]}`),
        (label) => (/unapproved|revenge|overtrading/.test(label) ? "bad" : "warn")
      )
    );
  }
  return box;
}

/* Mirrors `runtime/system_status.py`: `services` is a list of
 * {service, purpose, state, detail}, and feed / ai / deploy each carry a
 * `state` plus a human note. */
const SERVICE_TONE = {
  ok: "ok",
  alive: "ok",
  stale: "warn",
  late: "warn",
  never_seen: "bad",
  dead: "bad",
};

function renderHealth(body) {
  const box = panel("System health");
  if (!body) {
    box.appendChild(note("System status unavailable."));
    return box;
  }

  if (body.overall) {
    const tone = body.overall === "ok" ? "ok" : body.overall === "degraded" ? "warn" : "bad";
    box.appendChild(el("p", `spyder-tab__pill spyder-tab__pill--${tone}`, body.overall));
  }

  const services = Array.isArray(body.services) ? body.services : [];
  if (services.length) {
    const scroll = el("div", "spyder-tab__scroll");
    const table = el("table");
    const head = el("thead");
    const headRow = el("tr");
    for (const label of ["Service", "State", "Last seen"]) headRow.appendChild(el("th", null, label));
    head.appendChild(headRow);
    table.appendChild(head);
    const tbody = el("tbody");
    for (const info of services) {
      const tr = el("tr");
      const name = el("td", null, text(info.service));
      if (info.purpose) name.title = String(info.purpose);
      tr.appendChild(name);
      const state = el("td", null, text(info.state));
      const tone = SERVICE_TONE[info.state];
      if (tone) state.style.color = `var(--sd-${tone === "ok" ? "ok" : tone === "warn" ? "warn" : "bad"})`;
      if (info.detail) state.title = String(info.detail);
      tr.appendChild(state);
      tr.appendChild(
        el("td", null, info.last_seen ? ageLabel(info.last_seen) : info.age_seconds !== undefined && info.age_seconds !== null ? `${Math.round(Number(info.age_seconds))}s` : "—")
      );
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    scroll.appendChild(table);
    box.appendChild(scroll);
  }

  const section = (value) => (value && typeof value === "object" ? value : {});
  const feed = section(body.feed);
  const ai = section(body.ai);
  const deploy = section(body.deploy);
  const aiGate =
    ai.allowed_now === true
      ? `open${ai.reason ? ` (${ai.reason})` : ""}`
      : ai.allowed_now === false
        ? `closed${ai.reason ? ` (${ai.reason})` : ""}`
        : ai.state;
  box.appendChild(
    kv([
      ["Feed", feed.state],
      ["AI gate", aiGate],
      ["Deploy", deploy.commit || deploy.state],
    ])
  );

  // The notes are where the actionable detail lives ("market/ does not exist"),
  // so they are shown rather than collapsed into the state word above.
  for (const [label, value] of [
    ["Feed", feed.note],
    ["Deploy", deploy.note],
  ]) {
    if (value) box.appendChild(note(`${label}: ${value}`));
  }
  return box;
}

function renderDojo(dojo, validation) {
  const box = panel("Dojo & parity");
  if (dojo) {
    box.appendChild(
      kv([
        ["Dojo run", dojo.report_date],
        ["Generated", dojo.generated_at ? ageLabel(dojo.generated_at) : null],
        ["Summary", dojo.summary],
      ])
    );
    const flags = Array.isArray(dojo.flags) ? dojo.flags : [];
    if (flags.length) {
      box.appendChild(
        tags(
          flags.map((f) => text(f && f.flag ? f.flag : f)),
          // champion_promoted is the Dojo enacting a validated change — a good
          // outcome. Amber-ing it alongside a retention regression would read
          // as "something is wrong" on the one flag that means the opposite.
          (label) => {
            if (/promotion_write_failed|regress|fail/i.test(label)) return "bad";
            if (/^champion_promoted/.test(label)) return "ok";
            return "warn";
          }
        )
      );
    } else {
      box.appendChild(note("No Dojo flags."));
    }
  } else {
    box.appendChild(note("No Dojo report yet."));
  }

  if (validation) {
    const gates = Array.isArray(validation.gates) ? validation.gates.length : null;
    box.appendChild(
      kv([
        ["Parity", validation.ok === true ? "PASS" : validation.ok === false ? "FAIL" : "unknown"],
        ["Gates", gates],
        ["Checked", validation.generated_at ? ageLabel(validation.generated_at) : null],
      ])
    );
  } else {
    box.appendChild(note("No parity-validation report yet."));
  }
  return box;
}

function renderPositions(state) {
  const box = panel("Open positions");
  if (!state.openPositions.length) {
    box.appendChild(note("No open positions."));
    return box;
  }
  const scroll = el("div", "spyder-tab__scroll");
  const table = el("table");
  const head = el("thead");
  const headRow = el("tr");
  for (const label of ["Candidate", "Qty", "Entry", "Mark", "Unrealized"]) {
    headRow.appendChild(el("th", null, label));
  }
  head.appendChild(headRow);
  table.appendChild(head);
  const tbody = el("tbody");
  for (const position of state.openPositions) {
    const tr = el("tr");
    tr.appendChild(el("td", null, text(position.candidate_id)));
    tr.appendChild(el("td", null, text(position.open_contracts)));
    tr.appendChild(el("td", null, money(position.entry_price)));
    tr.appendChild(el("td", null, money(position.mark_price)));
    const pnl = el("td", signClass("spyder-tab__bar-value", position.unrealized_pnl), money(position.unrealized_pnl));
    tr.appendChild(pnl);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroll.appendChild(table);
  box.appendChild(scroll);
  return box;
}

/* -------------------------------------------------------------------------- */
/* Fetching                                                                   */
/* -------------------------------------------------------------------------- */

async function readJson(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  // A 404 here is a real answer ("no Dojo run has completed"), so it is
  // reported as absence rather than as a failure the user should chase.
  if (!response.ok) return { ok: false, status: response.status, body };
  return { ok: true, status: response.status, body };
}

/* -------------------------------------------------------------------------- */
/* Mount                                                                      */
/* -------------------------------------------------------------------------- */

export function mountSpyDerTab(options = {}) {
  const target =
    typeof options.target === "string"
      ? document.querySelector(options.target)
      : options.target || document.querySelector("[data-spy-der-tab]");
  if (!target) {
    throw new Error("mountSpyDerTab: no target element (expected [data-spy-der-tab])");
  }

  const base = (options.base || target.dataset.spyDerBase || "").replace(/\/$/, "");
  const endpoints = { ...DEFAULT_ENDPOINTS, ...(options.endpoints || {}) };
  const refreshMs = Number(options.refreshMs || target.dataset.spyDerRefresh || DEFAULT_REFRESH_MS);
  const url = (key) => `${base}${endpoints[key]}`;

  target.classList.add("spyder-tab");

  const head = el("div", "spyder-tab__head");
  const title = el("h2", "spyder-tab__title", "SPY-DER");
  const subtitle = el("small", null, "prediction · deterministic risk · guard");
  title.appendChild(subtitle);
  const statusPill = el("span", "spyder-tab__pill", "loading");
  const freshness = el("span", "spyder-tab__note", "");
  const refreshButton = el("button", "spyder-tab__refresh", "Refresh");
  refreshButton.type = "button";
  head.appendChild(title);
  head.appendChild(statusPill);
  head.appendChild(freshness);
  head.appendChild(el("span", "spyder-tab__spacer"));
  head.appendChild(refreshButton);

  const grid = el("div", "spyder-tab__grid");
  const footer = el("div", "spyder-tab__footer", "");

  clear(target);
  target.appendChild(head);
  target.appendChild(grid);
  target.appendChild(footer);

  let timer = null;
  let disposed = false;

  async function refresh() {
    refreshButton.disabled = true;
    const keys = ["system", "state", "dojo", "validation", "attribution"];
    const settled = await Promise.all(
      keys.map((key) =>
        readJson(url(key)).catch((error) => ({ ok: false, status: 0, body: null, error }))
      )
    );
    if (disposed) return;

    const results = {};
    keys.forEach((key, index) => {
      results[key] = settled[index];
    });

    const state = normalizeState(results.state.ok ? results.state.body : null);

    clear(grid);
    if (state) {
      statusPill.textContent = state.action;
      statusPill.className =
        "spyder-tab__pill" +
        (ACTION_TONE[state.action] ? ` spyder-tab__pill--${ACTION_TONE[state.action]}` : "");
      freshness.textContent = state.generatedAt
        ? `${state.symbol} · updated ${ageLabel(state.generatedAt)}`
        : state.symbol;
      grid.appendChild(renderChain(state));
      grid.appendChild(renderDecision(state));
    } else {
      statusPill.textContent = "no live state";
      statusPill.className = "spyder-tab__pill spyder-tab__pill--bad";
      freshness.textContent = "";
      const box = panel("Current decision", true);
      box.appendChild(
        note(
          results.state.status === 404
            ? "No live state published yet. The market service writes it on the first tick."
            : "Live state unreadable — check the state root and its permissions.",
          results.state.status !== 404
        )
      );
      grid.appendChild(box);
    }

    // Health sits beside the decision so the two single-width panels share a
    // row; the full-width shadow account follows rather than splitting them.
    grid.appendChild(renderHealth(results.system.ok ? results.system.body : null));
    grid.appendChild(renderAttribution(results.attribution.ok ? results.attribution.body : null));
    grid.appendChild(
      renderDojo(
        results.dojo.ok ? results.dojo.body : null,
        results.validation.ok ? results.validation.body : null
      )
    );
    if (state) grid.appendChild(renderPositions(state));

    const schema = state ? state.schema : "unknown";
    footer.textContent =
      `Read-only view · schema ${schema} · refreshed ${new Date().toLocaleTimeString()} · ` +
      "this tab cannot place, size, approve or promote anything.";
    refreshButton.disabled = false;
  }

  refreshButton.addEventListener("click", () => {
    void refresh();
  });
  void refresh();
  if (refreshMs > 0) {
    timer = setInterval(() => {
      void refresh();
    }, refreshMs);
  }

  return {
    refresh,
    dispose() {
      disposed = true;
      if (timer !== null) clearInterval(timer);
      clear(target);
      target.classList.remove("spyder-tab");
    },
  };
}

/* Auto-mount so embedding needs no JavaScript edit on the host page: a
 * container carrying `data-spy-der-tab` is enough. Hosts that manage their own
 * tab lifecycle should add `data-spy-der-manual` and call `mountSpyDerTab`
 * themselves when their tab is first shown. */
function autoMount() {
  for (const node of document.querySelectorAll("[data-spy-der-tab]")) {
    if (node.dataset.spyDerManual !== undefined) continue;
    if (node.dataset.spyDerMounted !== undefined) continue;
    node.dataset.spyDerMounted = "1";
    try {
      mountSpyDerTab({ target: node });
    } catch (error) {
      // Never let a mount failure break the host dashboard's own scripts.
      console.error("SPY-DER tab failed to mount", error);
    }
  }
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoMount);
  } else {
    autoMount();
  }
}

export default mountSpyDerTab;
