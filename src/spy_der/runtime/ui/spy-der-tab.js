/* SPY-DER dashboard tab — one implementation, two mounts.
 *
 * Mount A: 0DTE's Vercel dashboard, as the Adaptive Loop tab.
 * Mount B: SPY-DER's own dashboard API at `/ui`, which is what keeps working
 *          after 0DTE is retired.
 *
 * Both mounts load this same file, so there is no second copy to drift. The
 * 0DTE side needs no JavaScript edit: drop a container with `data-spy-der-tab`
 * on it and this module finds it. See
 * `integrations/zerodte/spy_der_tab/README.md`.
 *
 * Constraints this file is written to:
 *
 *   - No dependencies, no build step. It is loaded as a plain ES module.
 *   - Decision path stays closed. GETs render live state; operator POSTs only
 *     promote / reject / rollback *decision knobs* (champion.json). Nothing
 *     here can place, size, or submit a trade — the execution guard remains
 *     the only route to the market.
 *   - Operator writes need `data-spy-der-actions` (or options.actions) plus a
 *     Bearer token for SPY_DER_OPERATOR_TOKEN. Without both, Promote/Reject
 *     stay hidden and the tab is display-only.
 *   - No `innerHTML` with server data. Every value goes through `text()`, so a
 *     rationale or reason code cannot inject markup into 0DTE's page.
 *   - Panels fail independently. One unreadable artifact greys out its own
 *     panel and says why; it never blanks the tab.
 */

const DEFAULT_ENDPOINTS = {
  system: "/v1/system",
  state: "/v1/state",
  dojo: "/v1/dojo/latest",
  dojoProgress: "/v1/dojo/progress",
  pending: "/v1/dojo/pending",
  champion: "/v1/dojo/champion",
  promote: "/v1/dojo/promote",
  reject: "/v1/dojo/reject",
  rollback: "/v1/dojo/rollback",
  validation: "/v1/validation/latest",
  attribution: "/v1/attribution/latest",
};

const DEFAULT_REFRESH_MS = 30000;
const OPERATOR_TOKEN_KEY = "spyDerOperatorToken";

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

/* A 64-character snapshot hash or a 40-character commit SHA wraps onto three
 * lines on a phone and pushes the rest of the card out of shape. Only the
 * display is shortened — the full value stays in `title`, so it is still
 * readable and still copyable from the tooltip. */
function compact(value) {
  const str = String(value);
  // Hex only. Shortening on length alone also hit readable identifiers —
  // "grok-4.20-0309-non-reasoning" became "grok-4.20-…soning", which is worse
  // than letting it wrap. A digest carries no meaning in its middle; a model
  // name is nothing but meaning.
  if (!/^[0-9a-f]{24,}$/i.test(str)) return str;
  return `${str.slice(0, 10)}…${str.slice(-6)}`;
}

/* `hideEmpty` is opt-in rather than the default: elsewhere an em dash is the
 * point — the decision chain uses it to say a stage published nothing. It is
 * only noise where a whole group is empty for one understood reason, such as
 * a decision with no candidate to describe. */
function kv(pairs, { hideEmpty = false } = {}) {
  const list = el("dl", "spyder-tab__kv");
  for (const [key, value] of pairs) {
    if (hideEmpty && (value === null || value === undefined || value === "")) continue;
    list.appendChild(el("dt", null, key));
    const full = text(value);
    const shown = compact(full);
    const cell = el("dd", null, shown);
    if (shown !== full) cell.title = full;
    list.appendChild(cell);
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
  // With no candidate, Structure / Direction / Candidate / Reviewer are all
  // empty at once, and printing four em dashes said nothing four times. The
  // rationale below already explains why there is nothing to describe.
  const rows = kv(
    [
      ["Structure", state.structure],
      ["Direction", state.direction],
      ["Candidate", state.candidateId],
      ["Snapshot", state.snapshotId],
      ["Mode", state.mode],
      ["Trader", state.traderModel],
      ["Reviewer", state.reviewerModel],
    ],
    { hideEmpty: true }
  );
  if (rows.childElementCount) box.appendChild(rows);

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

/* Human labels for synthetic market archetypes. Keep in sync with
 * spy_der.synthetic.archetypes.ARCHETYPES — unknown ids fall back to spaces. */
const ARCHETYPE_LABELS = {
  calm_pin: "Calm pin",
  grind_up: "Grind up",
  grind_down: "Grind down",
  range_chop: "Range chop",
  vol_expansion: "Vol expansion",
  squeeze_melt_up: "Squeeze melt-up",
  crash: "Crash",
  gap_shock: "Gap shock",
};

function humanLabel(raw) {
  const key = text(raw, "");
  if (!key || key === "—") return "—";
  if (ARCHETYPE_LABELS[key]) return ARCHETYPE_LABELS[key];
  return key.replace(/_/g, " ");
}

function phasesOf(dojo) {
  const metrics = dojo && dojo.metrics && typeof dojo.metrics === "object" ? dojo.metrics : {};
  const phases = metrics.phases && typeof metrics.phases === "object" ? metrics.phases : {};
  return {
    recorded: phases.recorded && typeof phases.recorded === "object" ? phases.recorded : {},
    sequential: phases.sequential && typeof phases.sequential === "object" ? phases.sequential : {},
    learner: phases.learner && typeof phases.learner === "object" ? phases.learner : {},
    universe: phases.universe && typeof phases.universe === "object" ? phases.universe : {},
    promotion: phases.promotion && typeof phases.promotion === "object" ? phases.promotion : {},
  };
}

function dojoVerdict(dojo, phases) {
  const flags = Array.isArray(dojo.flags) ? dojo.flags : [];
  const flagNames = flags.map((f) => text(f && f.flag ? f.flag : f));
  const promoted = flagNames.some((f) => /^champion_promoted/.test(f));
  const rejected = flagNames.find((f) => /^promotion_rejected/.test(f));
  const remediation = phases.universe.remediation || {};
  const focus = Array.isArray(remediation.focus) ? remediation.focus : [];

  if (promoted) {
    return ["ok", "Promoted a safer setting", "The re-run beat the champion on every gate."];
  }
  if (rejected) {
    const gate = rejected.split(":").slice(1).join(" ").replace(/_/g, " ") || "a gate";
    return ["warn", "Change held back", `Blocked by ${gate} — champion unchanged.`];
  }
  if (focus.length) {
    const names = focus
      .slice(0, 2)
      .map((row) => humanLabel(row.label || row.archetype))
      .join(", ");
    return [
      "warn",
      "Gaps found — next sparring will focus there",
      remediation.headline || `Sampling will overweight ${names}.`,
    ];
  }
  if (phases.universe.status === "skipped") {
    return ["", "Sparring skipped", "Need more real market tape before synthetic worlds run."];
  }
  if (dojo.summary) {
    return ["ok", "Dojo run complete", text(dojo.summary)];
  }
  return ["", "Dojo run complete", "No major flags this run."];
}

function stageCard(name, value, detail, tone) {
  const card = el("div", "spyder-tab__stage" + (tone ? ` spyder-tab__stage--${tone}` : ""));
  card.appendChild(el("div", "spyder-tab__stage-name", name));
  card.appendChild(el("div", "spyder-tab__stage-value", text(value)));
  if (detail) card.appendChild(el("div", "spyder-tab__stage-detail", detail));
  return card;
}

function recordedStage(recorded) {
  const status = text(recorded.status, "unknown");
  if (status === "insufficient_data" || status === "skipped") {
    return stageCard("Real market tape", "Not enough yet", "Need more settled sessions.", "warn");
  }
  const evaluation = recorded.evaluation && typeof recorded.evaluation === "object" ? recorded.evaluation : {};
  const pnl = evaluation.total_pnl;
  const wr = evaluation.win_rate;
  const trades = evaluation.trades != null ? evaluation.trades : evaluation.n_matched;
  const tone = num(pnl) != null && num(pnl) < 0 ? "bad" : status === "ok" ? "ok" : "warn";
  const value =
    num(pnl) != null ? money(pnl) : status === "ok" ? "OK" : humanLabel(status);
  const detail = [
    wr != null ? `win rate ${pct(wr)}` : null,
    trades != null ? `${trades} trades` : null,
    recorded.n_sessions != null ? `${recorded.n_sessions} sessions` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return stageCard("Real market tape", value, detail || "Scored against settled outcomes.", tone);
}

function sequentialStage(sequential) {
  const status = text(sequential.status, "unknown");
  if (status === "skipped" || status === "insufficient_data") {
    return stageCard("Blind-day check", "Skipped", "Not enough days for a leak-free walk.", "");
  }
  const ft = num(sequential.mean_forward_transfer);
  const retention = sequential.retention && typeof sequential.retention === "object" ? sequential.retention : {};
  const retentionOk = retention.ok !== false;
  let tone = "ok";
  let value = "Held up";
  if (ft != null && ft < 0) {
    tone = "bad";
    value = "Behind baseline";
  } else if (!retentionOk) {
    tone = "bad";
    value = "Forgot prior days";
  } else if (status !== "ok") {
    tone = "warn";
    value = humanLabel(status);
  }
  const detail = [
    ft != null ? `transfer ${ft >= 0 ? "+" : ""}${ft.toFixed(2)}` : null,
    retentionOk ? "retention OK" : "retention slipped",
  ]
    .filter(Boolean)
    .join(" · ");
  return stageCard("Blind-day check", value, detail || "Leak-free forward walk.", tone);
}

function learnerStage(learner, promotion) {
  const outcome = text(learner.outcome || learner.status, "idle");
  const enacted = promotion && promotion.enacted === true;
  if (enacted || outcome === "promoted") {
    const knobs = promotion && promotion.staged_changes ? Object.keys(promotion.staged_changes) : [];
    return stageCard(
      "Adaptive change",
      "Promoted",
      knobs.length ? `Now live: ${knobs.map(humanLabel).join(", ")}` : "Champion updated after a winning re-run.",
      "ok"
    );
  }
  if (/^promotion_rejected/.test(outcome) || (promotion && promotion.status === "rejected")) {
    return stageCard("Adaptive change", "Held back", "Re-run did not clear every gate.", "warn");
  }
  if (outcome === "promotion_recommended" || outcome === "staged") {
    return stageCard("Adaptive change", "Staged", "Waiting for a validating re-run.", "warn");
  }
  if (outcome === "no_change" || outcome === "gated" || outcome === "hold") {
    return stageCard("Adaptive change", "No change", "Champion left alone this run.", "ok");
  }
  return stageCard("Adaptive change", humanLabel(outcome), "Diagnose → hypothesize → stage.", "");
}

function sparringStage(universe) {
  const status = text(universe.status, "unknown");
  if (status === "skipped") {
    return stageCard("Synthetic sparring", "Skipped", "Refused until real tape is thick enough.", "warn");
  }
  if (status === "unscored") {
    return stageCard("Synthetic sparring", "Unscored", "Worlds generated but not scored.", "bad");
  }
  const remediation = universe.remediation || {};
  const weak = Array.isArray(remediation.weak_archetypes) ? remediation.weak_archetypes : [];
  const n = universe.n_universes != null ? universe.n_universes : 0;
  const tone = weak.length ? "warn" : status === "ok" ? "ok" : "warn";
  const value = weak.length ? `${weak.length} weak type${weak.length === 1 ? "" : "s"}` : `${n} worlds`;
  const detail = weak.length
    ? `${n} worlds · next run overweighting the losers`
    : status === "ok"
      ? `${n} worlds · no losing market types`
      : humanLabel(status);
  return stageCard("Synthetic sparring", value, detail, tone);
}

function renderFocus(universe) {
  const remediation = universe.remediation || {};
  const focus = Array.isArray(remediation.focus) ? remediation.focus : [];
  const wrap = el("div", "spyder-tab__focus");
  wrap.appendChild(el("h4", "spyder-tab__subhead", "Tonight’s focus"));
  const headline = text(remediation.headline, "");
  if (headline && headline !== "—") {
    wrap.appendChild(el("p", "spyder-tab__focus-headline", headline));
  } else if (!focus.length) {
    wrap.appendChild(note("No elevated sampling targets — sparring stays balanced."));
    return wrap;
  }
  if (focus.length) {
    const list = el("ul", "spyder-tab__focus-list");
    for (const row of focus) {
      const label = humanLabel(row.label || row.archetype);
      const reasons = Array.isArray(row.reasons) ? row.reasons.filter(Boolean) : [];
      const reasonText = reasons.length ? reasons.join("; ") : null;
      const item = el("li", "spyder-tab__focus-item");
      item.appendChild(el("div", "spyder-tab__focus-name", label));
      if (reasonText) {
        item.appendChild(el("div", "spyder-tab__focus-reason", reasonText));
      }
      list.appendChild(item);
    }
    wrap.appendChild(list);
  }
  const priorNote = text(remediation.prior_note, "");
  if (priorNote && priorNote !== "—") {
    wrap.appendChild(note(priorNote));
  } else if (remediation.prior_influenced_sampling) {
    wrap.appendChild(note("This run sampled with last night’s gap weights."));
  } else if (remediation.prior_blended_into_plan) {
    wrap.appendChild(
      note("Prior curriculum was blended into the next-run plan (lattice measurement does not re-sample).")
    );
  }
  return wrap;
}

function renderRobustness(universe) {
  const matrix = universe.archetype_matrix && typeof universe.archetype_matrix === "object"
    ? universe.archetype_matrix
    : null;
  const wrap = el("div", "spyder-tab__robustness");
  wrap.appendChild(el("h4", "spyder-tab__subhead", "Where we’re weak"));
  if (!matrix || !Object.keys(matrix).length) {
    wrap.appendChild(note("No robustness matrix yet — sparring has not produced scored worlds."));
    return wrap;
  }

  const scroll = el("div", "spyder-tab__scroll");
  const table = el("table", "spyder-tab__matrix");
  const head = el("thead");
  const headRow = el("tr");
  for (const label of ["Market type", "P&L", "Win rate", "Sessions"]) {
    headRow.appendChild(el("th", null, label));
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const tbody = el("tbody");
  const rows = Object.entries(matrix).sort((a, b) => {
    const pa = num(a[1] && a[1].mean_session_pnl);
    const pb = num(b[1] && b[1].mean_session_pnl);
    if (pa == null && pb == null) return a[0].localeCompare(b[0]);
    if (pa == null) return 1;
    if (pb == null) return -1;
    return pa - pb;
  });

  for (const [arch, metrics] of rows) {
    const tr = el("tr");
    const mean = num(metrics.mean_session_pnl);
    const weak = mean != null && mean < 0;
    if (weak) tr.className = "spyder-tab__matrix-row--weak";
    tr.appendChild(el("td", null, humanLabel(arch)));
    tr.appendChild(
      el("td", signClass("spyder-tab__bar-value", mean), mean != null ? money(mean) : "—")
    );
    tr.appendChild(el("td", null, pct(metrics.session_win_rate)));
    tr.appendChild(el("td", null, text(metrics.n_sessions, "0")));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroll.appendChild(table);
  wrap.appendChild(scroll);

  const visited = universe.coverage_cells_visited;
  const total = universe.coverage_cells_total;
  if (visited != null && total != null) {
    const unvisited = universe.remediation && universe.remediation.unvisited_count;
    const coverageNote =
      unvisited != null && unvisited > 0
        ? `Situation coverage: ${visited} of ${total} cells visited · ${unvisited} still unseen.`
        : `Situation coverage: ${visited} of ${total} cells visited.`;
    wrap.appendChild(note(coverageNote));
  }
  return wrap;
}

function renderHumanStory(human) {
  if (!human || typeof human !== "object") return null;
  const wrap = el("div", "spyder-tab__story");
  const lines = [
    ["What this checked", human.what_ran],
    ["Data used", human.data_story],
    ["Why it stopped", human.stop_reason],
    ["What changes", human.change],
    ["Next", human.next_step],
  ];
  for (const [label, value] of lines) {
    if (!value) continue;
    const row = el("div", "spyder-tab__story-row");
    row.appendChild(el("div", "spyder-tab__story-label", label));
    row.appendChild(el("div", "spyder-tab__story-body", text(value)));
    wrap.appendChild(row);
  }
  return wrap.firstChild ? wrap : null;
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${mins}m`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

function progressTone(status) {
  if (status === "running" || status === "live") return "ok";
  if (status === "failed" || status === "stale") return "bad";
  if (status === "finished") return "ok";
  return "";
}

function progressHeadline(progress) {
  const status = text(progress && progress.status, "idle");
  if (status === "running") return "WORKING";
  if (status === "finished") return "FINISHED";
  if (status === "failed") return "FAILED";
  if (status === "stale") return "STALE";
  return "IDLE";
}

/* Returns null when nothing is published. No progress feed is not the same as
 * a Dojo sitting idle: rendering the skeleton for an absent payload invented a
 * run that was not happening — an "IDLE" pill over five PENDING stage cards,
 * directly above the completed report those cards contradict. It is the same
 * mistake the decision chain is careful to avoid, and it shows up on every
 * mount that cannot read /v1/dojo/progress. Absent means absent. */
function renderDojoProgress(progress) {
  const payload = progress && typeof progress === "object" ? progress : null;
  if (!payload) return null;

  const square = el("div", "spyder-tab__dojo-square");
  const status = text(payload.status, "idle");
  const live = Boolean(payload && payload.live);

  const head = el("div", "spyder-tab__dojo-square-head");
  const pill = el(
    "p",
    "spyder-tab__pill" +
      (progressTone(status) ? ` spyder-tab__pill--${progressTone(status)}` : "") +
      (live ? " spyder-tab__pill--pulse" : "")
  );
  pill.textContent = progressHeadline(payload);
  head.appendChild(pill);
  if (payload && payload.phase_label && status !== "idle") {
    head.appendChild(el("span", "spyder-tab__dojo-square-phase", text(payload.phase_label)));
  }
  if (payload && Number(payload.elapsed_seconds) > 0) {
    head.appendChild(
      el("span", "spyder-tab__note", `elapsed ${formatElapsed(payload.elapsed_seconds)}`)
    );
  }
  square.appendChild(head);

  const detail = el(
    "p",
    "spyder-tab__dojo-square-detail",
    payload && payload.detail
      ? text(payload.detail)
      : "Waiting for the next Dojo oneshot."
  );
  square.appendChild(detail);

  const strip = el("div", "spyder-tab__dojo-strip");
  const phases = Array.isArray(payload && payload.phases) ? payload.phases : [];
  const defaults = [
    ["recorded", "Real tape"],
    ["sequential", "Blind days"],
    ["learner", "Adaptive change"],
    ["universe", "Synthetic sparring"],
    ["promotion", "Promotion trial"],
  ];
  const entries = phases.length
    ? phases
    : defaults.map(([name, label]) => ({ name, label, status: "pending", detail: "" }));
  for (const phase of entries) {
    const cell = el(
      "div",
      "spyder-tab__dojo-step spyder-tab__dojo-step--" + text(phase.status, "pending")
    );
    cell.appendChild(el("div", "spyder-tab__dojo-step-label", text(phase.label || phase.name)));
    cell.appendChild(el("div", "spyder-tab__dojo-step-status", text(phase.status, "pending")));
    if (phase.detail) {
      cell.title = text(phase.detail);
    }
    strip.appendChild(cell);
  }
  square.appendChild(strip);
  return square;
}

function knobsSummary(knobs) {
  if (!knobs || typeof knobs !== "object") return "—";
  const parts = Object.entries(knobs)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 6)
    .map(([key, value]) => `${key}=${value}`);
  return parts.length ? parts.join(", ") : "—";
}

function renderPendingReview(pendingBody, actions) {
  const box = el("div", "spyder-tab__pending");
  box.appendChild(el("h4", "spyder-tab__subhead", "Knob challengers · pending review"));

  const pending = pendingBody && Array.isArray(pendingBody.pending) ? pendingBody.pending : [];
  const actionsEnabled = Boolean(pendingBody && pendingBody.actions_enabled);
  const canAct = Boolean(actions && actions.enabled);

  if (!pending.length) {
    box.appendChild(
      note(
        "No staged knob challengers. Auto-promote clears this queue when a trial passes; " +
          "with SPY_DER_DOJO_AUTO_PROMOTE=0 the Dojo leaves candidates here for you."
      )
    );
  } else {
    const list = el("div", "spyder-tab__pending-list");
    for (const candidate of pending) {
      const row = el("div", "spyder-tab__pending-row");
      if (actions && actions.selectedId === candidate.candidate_id) {
        row.classList.add("spyder-tab__pending-row--selected");
      }
      row.appendChild(
        el("div", "spyder-tab__pending-id", text(candidate.candidate_id))
      );
      const meta = el("div", "spyder-tab__pending-meta");
      meta.appendChild(
        el(
          "span",
          null,
          candidate.target_archetype
            ? `focus ${text(candidate.target_archetype)}`
            : text(candidate.mode, "knob change")
        )
      );
      meta.appendChild(el("span", "spyder-tab__note", knobsSummary(candidate.knobs)));
      row.appendChild(meta);

      if (canAct) {
        const btns = el("div", "spyder-tab__pending-actions");
        const promoteBtn = el("button", "spyder-tab__btn spyder-tab__btn--ok", "Promote");
        promoteBtn.type = "button";
        promoteBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          void actions.onPromote(candidate.candidate_id);
        });
        const rejectBtn = el("button", "spyder-tab__btn spyder-tab__btn--bad", "Reject");
        rejectBtn.type = "button";
        rejectBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          void actions.onReject(candidate.candidate_id);
        });
        btns.appendChild(promoteBtn);
        btns.appendChild(rejectBtn);
        row.appendChild(btns);
      } else {
        row.addEventListener("click", () => {
          if (actions && actions.onSelect) actions.onSelect(candidate.candidate_id);
        });
      }
      list.appendChild(row);
    }
    box.appendChild(list);
  }

  if (actions && actions.enabled) {
    const unlock = el("div", "spyder-tab__operator");
    if (actions.hasToken) {
      unlock.appendChild(
        note(
          actionsEnabled
            ? "Operator unlocked — Promote / Reject write decision knobs only (not forecast models)."
            : "Token saved locally, but the API has no SPY_DER_OPERATOR_TOKEN configured."
        )
      );
      const rollbackBtn = el("button", "spyder-tab__btn", "Rollback champion");
      rollbackBtn.type = "button";
      rollbackBtn.addEventListener("click", () => {
        void actions.onRollback();
      });
      const lockBtn = el("button", "spyder-tab__btn", "Lock");
      lockBtn.type = "button";
      lockBtn.addEventListener("click", () => actions.onLock());
      unlock.appendChild(rollbackBtn);
      unlock.appendChild(lockBtn);
    } else {
      unlock.appendChild(
        note(
          "Unlock with the operator token to Promote / Reject staged knob challengers."
        )
      );
      const row = el("div", "spyder-tab__operator-row");
      const input = el("input", "spyder-tab__operator-input");
      input.type = "password";
      input.placeholder = "SPY_DER_OPERATOR_TOKEN";
      input.autocomplete = "off";
      const unlockBtn = el("button", "spyder-tab__btn spyder-tab__btn--ok", "Unlock");
      unlockBtn.type = "button";
      unlockBtn.addEventListener("click", () => {
        actions.onUnlock(input.value);
      });
      row.appendChild(input);
      row.appendChild(unlockBtn);
      unlock.appendChild(row);
    }
    if (actions.message) {
      unlock.appendChild(
        note(actions.message, /fail|refus|unauth|error/i.test(actions.message))
      );
    }
    box.appendChild(unlock);
  } else if (pending.length) {
    box.appendChild(
      note(
        "Display only on this mount — enable data-spy-der-actions to Promote / Reject."
      )
    );
  }
  return box;
}

function renderDojo(dojo, validation, progress, pendingBody, actions) {
  const box = panel("Adaptive Loop · Dojo", true);
  const progressPanel = renderDojoProgress(progress);
  if (progressPanel) box.appendChild(progressPanel);
  box.appendChild(renderPendingReview(pendingBody, actions));
  if (!dojo) {
    box.appendChild(note("No Dojo report yet. The nightly run will fill this in."));
  } else {
    const phases = phasesOf(dojo);
    const human = dojo.human && typeof dojo.human === "object" ? dojo.human : null;
    const [tone, title, blurb] = dojoVerdict(dojo, phases);

    const verdict = el("p", "spyder-tab__pill" + (tone ? ` spyder-tab__pill--${tone}` : ""));
    verdict.textContent = human && human.headline ? text(human.headline) : title;
    box.appendChild(verdict);
    if (blurb && !(human && human.headline)) box.appendChild(note(blurb));

    box.appendChild(
      kv([
        ["Run date", dojo.report_date],
        ["Generated", dojo.generated_at ? ageLabel(dojo.generated_at) : null],
      ])
    );

    const story = renderHumanStory(human);
    if (story) box.appendChild(story);

    if (phases.universe && (phases.universe.remediation || phases.universe.archetype_matrix)) {
      box.appendChild(renderFocus(phases.universe));
    }

    const chain = el("div", "spyder-tab__chain spyder-tab__chain--dojo");
    chain.appendChild(recordedStage(phases.recorded));
    chain.appendChild(sequentialStage(phases.sequential));
    chain.appendChild(learnerStage(phases.learner, phases.promotion));
    chain.appendChild(sparringStage(phases.universe));
    box.appendChild(chain);

    if (phases.universe && phases.universe.archetype_matrix) {
      box.appendChild(renderRobustness(phases.universe));
    }

    const flags = Array.isArray(dojo.flags) ? dojo.flags : [];
    const humanFlags = flags
      .map((f) => text(f && f.flag ? f.flag : f))
      .filter((label) => label && label !== "—")
      .map((label) => humanLabel(label.replace(/^weak_archetype:/, "weak: ")));
    if (humanFlags.length) {
      box.appendChild(el("h4", "spyder-tab__subhead", "Flags"));
      box.appendChild(
        tags(humanFlags, (label) => {
          if (/promotion write failed|regress|fail|weak:/i.test(label)) return "bad";
          if (/champion promoted|promoted/i.test(label)) return "ok";
          return "warn";
        })
      );
    }
  }

  const parity = el("div", "spyder-tab__parity");
  parity.appendChild(el("h4", "spyder-tab__subhead", "Parity check"));
  if (validation) {
    const ok = validation.ok === true;
    const fail = validation.ok === false;
    parity.appendChild(
      kv([
        ["Result", ok ? "PASS" : fail ? "FAIL" : "unknown"],
        ["Gates", Array.isArray(validation.gates) ? validation.gates.length : null],
        ["Checked", validation.generated_at ? ageLabel(validation.generated_at) : null],
      ])
    );
  } else {
    parity.appendChild(note("No parity-validation report yet."));
  }
  box.appendChild(parity);
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

async function readJson(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const response = await fetch(url, {
    method: options.method || "GET",
    headers,
    body: options.body,
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  // A 404 here is a real answer ("no Dojo run has completed"), so it is
  // reported as absence rather than as a failure the user should chase.
  if (!response.ok) return { ok: false, status: response.status, body };
  return { ok: true, status: response.status, body };
}

function loadOperatorToken() {
  try {
    return sessionStorage.getItem(OPERATOR_TOKEN_KEY) || "";
  } catch (_err) {
    return "";
  }
}

function saveOperatorToken(token) {
  try {
    if (token) sessionStorage.setItem(OPERATOR_TOKEN_KEY, token);
    else sessionStorage.removeItem(OPERATOR_TOKEN_KEY);
  } catch (_err) {
    /* private mode — actions still work for this page lifetime via closure */
  }
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
  const actionsEnabled =
    options.actions === true ||
    target.dataset.spyDerActions !== undefined;
  const url = (key) => `${base}${endpoints[key]}`;

  target.classList.add("spyder-tab");

  const head = el("div", "spyder-tab__head");
  const title = el("h2", "spyder-tab__title", "SPY-DER");
  const subtitle = el("small", null, "prediction · risk · Adaptive Loop · Dojo");
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
  let operatorToken = options.operatorToken || loadOperatorToken();
  let selectedId = null;
  let actionMessage = "";
  let actionBusy = false;

  async function postAction(key, payload) {
    if (actionBusy) return;
    if (!operatorToken) {
      actionMessage = "Unlock with the operator token first.";
      void refresh();
      return;
    }
    actionBusy = true;
    actionMessage = "Working…";
    void refresh();
    const result = await readJson(url(key), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${operatorToken}`,
        // Vercel→0DTE rewrites Authorization to DASHBOARD_TOKEN; this header
        // carries the operator secret through that hop to spy-der-dashboard-api.
        "X-Spy-Der-Operator-Token": operatorToken,
      },
      body: JSON.stringify(payload || {}),
    }).catch((error) => ({ ok: false, status: 0, body: { detail: String(error) } }));
    actionBusy = false;
    if (result.ok) {
      actionMessage = `${text(result.body && result.body.action, key)} ok`;
      selectedId = null;
    } else {
      const detail =
        (result.body && (result.body.detail || result.body.error)) ||
        `HTTP ${result.status}`;
      actionMessage = String(detail);
      if (result.status === 401) {
        operatorToken = "";
        saveOperatorToken("");
      }
    }
    void refresh();
  }

  const actions = actionsEnabled
    ? {
        get enabled() {
          return true;
        },
        get hasToken() {
          return Boolean(operatorToken);
        },
        get selectedId() {
          return selectedId;
        },
        get message() {
          return actionMessage;
        },
        onSelect(id) {
          selectedId = id;
          void refresh();
        },
        onUnlock(token) {
          operatorToken = String(token || "").trim();
          saveOperatorToken(operatorToken);
          actionMessage = operatorToken ? "Unlocked for this browser session." : "";
          void refresh();
        },
        onLock() {
          operatorToken = "";
          saveOperatorToken("");
          actionMessage = "Locked.";
          void refresh();
        },
        onPromote(id) {
          return postAction("promote", { candidate_id: id, human_ack: "PROMOTE" });
        },
        onReject(id) {
          return postAction("reject", { candidate_id: id });
        },
        onRollback() {
          return postAction("rollback", {});
        },
      }
    : null;

  async function refresh() {
    refreshButton.disabled = true;
    const keys = [
      "system",
      "state",
      "dojo",
      "dojoProgress",
      "pending",
      "validation",
      "attribution",
    ];
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
        results.validation.ok ? results.validation.body : null,
        results.dojoProgress.ok ? results.dojoProgress.body : null,
        results.pending.ok ? results.pending.body : { pending: [], actions_enabled: false },
        actions
      )
    );
    if (state) grid.appendChild(renderPositions(state));

    const schema = state ? state.schema : "unknown";
    const pendingCount =
      results.pending.ok && results.pending.body
        ? Number(results.pending.body.count || 0)
        : 0;
    footer.textContent = actionsEnabled
      ? `Adaptive Loop · schema ${schema} · ${pendingCount} pending knob challenger(s) · ` +
        `refreshed ${new Date().toLocaleTimeString()} · trades still go only through the execution guard.`
      : `Display view · schema ${schema} · refreshed ${new Date().toLocaleTimeString()} · ` +
        "Promote/Reject hidden on this mount (no data-spy-der-actions).";
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
