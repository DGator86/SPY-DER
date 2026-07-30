/**
 * Vercel gateway for the SPY-DER dashboard page.
 *
 * Routing
 * -------
 * `vercel.json` rewrites `/api/:path*` here and passes the tail as `__path`.
 * The previous layout relied on a zero-config catch-all (`api/[...path].js`),
 * which only ever matched ONE segment in production: `/api/health` reached the
 * function but `/api/v1/system` returned Vercel's own 404. Every endpoint this
 * dashboard reads lives under `/v1/...`, so the whole page was dark. The
 * rewrite makes the mapping explicit instead of inferred.
 *
 * Modes
 * -----
 * native — SPY_DER_DASHBOARD_URL points at `spy-der-dashboard-api` (the VPS
 *          service that binds 127.0.0.1:8788, exposed through a tunnel).
 *          Subpaths forward untouched, so the full /v1 surface works,
 *          including operator promote / reject / rollback.
 *
 * bridge — no direct tunnel exists yet. SPY-DER state is still readable through
 *          the 0DTE host, which publishes it from the same files on the same
 *          box: `/api/spy-der` (live packet + Dojo report) and `/api/system`
 *          (service health, feed, deployed commit). This mode carves the /v1
 *          responses the tab expects out of those two documents so the page
 *          shows real data with no VPS work. Read-only by construction.
 *
 * Set SPY_DER_DASHBOARD_URL to a real tunnel and the page upgrades itself to
 * native on the next request — no code change, no redeploy.
 */

/** 0DTE's public host. Reachable today; retired once SPY-DER has its own tunnel. */
const DEFAULT_BRIDGE_ORIGIN = "https://0-dte-kappa.vercel.app";

/** A configured URL ending in this is the documented 0DTE hop, not a direct API. */
const BRIDGE_SUFFIX = "/api/spy-der";

/** /v1/state and /v1/dojo/latest are carved from one upstream document and are
 *  always requested together, so a warm instance fetches it once per refresh. */
const BUNDLE_TTL_MS = 5000;
let bundleCache = { origin: "", at: 0, status: 0, body: null };

/** Endpoints that exist natively but have no bridge equivalent. Reported as 404
 *  with a reason: the tab treats 404 as "not published yet" and greys out that
 *  panel alone, which is the honest rendering — the data is genuinely not
 *  reachable through this hop, as opposed to broken. */
const BRIDGE_ABSENT = {
  "v1/dojo/progress": "live Dojo progress",
  "v1/dojo/pending": "pending knob challengers",
  "v1/dojo/champion": "champion knobs",
  "v1/validation/latest": "validation reports",
  "v1/attribution/latest": "the shadow-account attribution waterfall",
};

const OPERATOR_WRITES = /^v1\/dojo\/(promote|reject|rollback)\/?$/;

function json(res, status, body, mode) {
  res.status(status);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  if (mode) res.setHeader("X-Spy-Der-Mode", mode);
  return res.send(JSON.stringify(body));
}

/**
 * Work out where to read from, and how.
 *
 * Guards against the page being pointed at itself: that URL is the natural
 * thing to paste when asked for "the dashboard URL", and it would make the
 * function call its own rewrite until the invocation times out.
 */
function resolveUpstream(req) {
  const configured = (
    process.env.SPY_DER_DASHBOARD_URL ||
    process.env.VPS_API_URL ||
    ""
  )
    .trim()
    .replace(/\/+$/, "");

  const forced = (process.env.SPY_DER_DASHBOARD_MODE || "").trim().toLowerCase();

  if (!configured) {
    return {
      mode: "bridge",
      origin: DEFAULT_BRIDGE_ORIGIN,
      configured: false,
      reason: "SPY_DER_DASHBOARD_URL unset — reading SPY-DER state through the 0DTE host",
    };
  }

  let parsed;
  try {
    parsed = new URL(configured);
  } catch (_err) {
    return { error: `SPY_DER_DASHBOARD_URL is not a valid URL: ${configured}` };
  }

  const selfHost = String(req.headers["x-forwarded-host"] || req.headers.host || "")
    .split(",")[0]
    .trim()
    .toLowerCase();
  if (selfHost && parsed.host.toLowerCase() === selfHost) {
    return {
      error:
        `SPY_DER_DASHBOARD_URL points at this page (${parsed.host}), which would ` +
        "make it proxy to itself. Set it to the spy-der-dashboard-api tunnel " +
        "(the service on 127.0.0.1:8788), or unset it to read through the 0DTE host.",
    };
  }

  const looksLikeBridge = parsed.pathname.replace(/\/+$/, "").endsWith(BRIDGE_SUFFIX);
  const mode = forced === "native" || forced === "bridge" ? forced : looksLikeBridge ? "bridge" : "native";

  if (mode === "bridge") {
    // Accept either the hop itself (…/api/spy-der) or a bare origin.
    const origin = configured.replace(new RegExp(`${BRIDGE_SUFFIX}$`), "").replace(/\/+$/, "");
    return { mode, origin: origin || parsed.origin, configured: true };
  }
  return { mode, base: configured, configured: true };
}

async function fetchJson(target, headers) {
  const upstream = await fetch(target, { headers });
  const body = await upstream.text();
  let parsed = null;
  try {
    parsed = JSON.parse(body);
  } catch (_err) {
    parsed = null;
  }
  return { status: upstream.status, body: parsed, raw: body };
}

/** The 0DTE bundle: `{schema_version, live, dojo, dojo_status}`. */
async function readBundle(origin) {
  const now = Date.now();
  if (bundleCache.origin === origin && now - bundleCache.at < BUNDLE_TTL_MS) {
    return { status: bundleCache.status, body: bundleCache.body };
  }
  const result = await fetchJson(`${origin}${BRIDGE_SUFFIX}`, { Accept: "application/json" });
  bundleCache = { origin, at: now, status: result.status, body: result.body };
  return result;
}

/**
 * A soft note in place of a packet means the file was missing or unreadable on
 * the VPS. Surfacing that as 404 keeps "never written" distinct from "the hop
 * is down", which the tab renders differently.
 */
function isSoftNote(value) {
  return (
    !value ||
    typeof value !== "object" ||
    (typeof value.note === "string" && value.schema_version === undefined && value.human === undefined)
  );
}

async function serveBridge(res, subpath, method, upstream) {
  const { origin } = upstream;

  if (method === "POST") {
    return json(
      res,
      503,
      {
        detail:
          "Operator actions need a direct connection to spy-der-dashboard-api. " +
          "The 0DTE bridge is read-only: it publishes SPY-DER state from files and " +
          "cannot promote, reject or roll back a challenger. Expose 127.0.0.1:8788 " +
          "through a tunnel and set SPY_DER_DASHBOARD_URL to it.",
        mode: "bridge",
      },
      "bridge"
    );
  }

  if (subpath === "v1/system") {
    const { status, body } = await fetchJson(`${origin}/api/system`, { Accept: "application/json" });
    if (status >= 400 || !body) {
      return json(res, status || 502, { detail: "system status unavailable through the bridge" }, "bridge");
    }
    return json(res, 200, body, "bridge");
  }

  if (subpath === "v1/state" || subpath === "v1/dojo/latest") {
    const { status, body } = await readBundle(origin);
    if (status >= 400 || !body) {
      return json(res, status || 502, { detail: "SPY-DER bundle unavailable through the bridge" }, "bridge");
    }
    const wanted = subpath === "v1/state" ? body.live : body.dojo;
    if (isSoftNote(wanted)) {
      return json(
        res,
        404,
        {
          detail:
            (wanted && wanted.note) ||
            (subpath === "v1/state" ? "no live state published yet" : "no Dojo report published yet"),
          mode: "bridge",
        },
        "bridge"
      );
    }
    return json(res, 200, wanted, "bridge");
  }

  const absent = BRIDGE_ABSENT[subpath];
  if (absent) {
    return json(
      res,
      404,
      {
        detail:
          `${absent} is not published through the 0DTE bridge. It becomes available once ` +
          "SPY_DER_DASHBOARD_URL points at spy-der-dashboard-api directly.",
        mode: "bridge",
      },
      "bridge"
    );
  }

  return json(res, 404, { detail: `no bridge mapping for /${subpath}`, mode: "bridge" }, "bridge");
}

async function serveNative(req, res, subpath, search, upstream) {
  const token = (process.env.DASHBOARD_TOKEN || "").trim();
  const headers = { Accept: req.headers.accept || "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  // The operator secret rides its own header so Authorization stays free for
  // the hop's own bearer token.
  const operator = req.headers["x-spy-der-operator-token"];
  if (operator) headers["X-Spy-Der-Operator-Token"] = operator;

  let body;
  if (req.method === "POST") {
    headers["Content-Type"] = req.headers["content-type"] || "application/json";
    if (typeof req.body === "string") body = req.body;
    else if (typeof Buffer !== "undefined" && Buffer.isBuffer(req.body)) body = req.body;
    else body = JSON.stringify(req.body ?? {});
  }

  const target = `${upstream.base}/${subpath}${search}`;
  const response = await fetch(target, { method: req.method, headers, body });
  const text = await response.text();

  res.status(response.status);
  res.setHeader("Content-Type", response.headers.get("content-type") || "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Spy-Der-Mode", "native");
  return res.send(text);
}

export default async function handler(req, res) {
  const requestUrl = new URL(req.url, "http://localhost");

  // `__path` comes from the rewrite. Falling back to the raw pathname keeps the
  // function correct if it is ever reached directly rather than through it.
  const params = requestUrl.searchParams;
  const clean = (value) => String(value || "").replace(/^\/+/, "").replace(/\/+$/, "");

  // Three ways the tail can arrive, tried in order of trustworthiness. The
  // rewrite is `/api/:path* -> /api/proxy?__path=:path*`, but neither half of
  // that is guaranteed: a token that fails to expand arrives literally as
  // ":path*", and whether req.url carries the original path or the rewritten
  // destination is a routing-layer detail. Vercel also auto-appends any source
  // param the destination path does not consume, which is the third form.
  // Guessing wrong here is what made the whole page 404, so all three are read
  // rather than assumed.
  const explicit = params.get("__path");
  params.delete("__path");
  const fromPathname = clean(requestUrl.pathname.replace(/^\/api\/?/, ""));
  const auto = params.get("path");

  let subpath = "";
  if (explicit && !explicit.includes(":")) {
    subpath = clean(explicit);
  } else if (fromPathname && fromPathname !== "proxy") {
    subpath = fromPathname;
  } else if (auto && !auto.includes(":")) {
    subpath = clean(auto);
    // Only injected by the rewrite in this branch, so it is ours to consume —
    // forwarding it would append a query the upstream never asked for.
    params.delete("path");
  }

  if (!subpath && fromPathname === "proxy") {
    return json(res, 500, {
      detail:
        "The /api rewrite did not carry the request path. Check the rewrite in " +
        "vercel.json: /api/:path* must forward the tail to /api/proxy.",
    });
  }

  const qs = params.toString();
  const search = qs ? `?${qs}` : "";

  if (req.method === "OPTIONS") {
    res.setHeader("Cache-Control", "no-store");
    return res.status(204).end();
  }

  const operatorWrite = req.method === "POST" && OPERATOR_WRITES.test(subpath);
  if (!["GET", "HEAD"].includes(req.method) && !operatorWrite) {
    return json(res, 405, {
      detail: "Method not allowed — this dashboard is read-only apart from decision-knob writes",
    });
  }

  const upstream = resolveUpstream(req);
  if (upstream.error) {
    return json(res, 500, { detail: upstream.error });
  }

  // Diagnostic for the page shell's connection indicator. Reports the host it
  // reads from, never the token.
  if (subpath === "__status") {
    return json(
      res,
      200,
      {
        mode: upstream.mode,
        upstream: upstream.mode === "bridge" ? upstream.origin : upstream.base,
        configured: Boolean(upstream.configured),
        reason: upstream.reason || null,
        operator_actions: upstream.mode === "native",
        checked_at: new Date().toISOString(),
      },
      upstream.mode
    );
  }

  try {
    if (upstream.mode === "bridge") {
      return await serveBridge(res, subpath, req.method, upstream);
    }
    return await serveNative(req, res, subpath, search, upstream);
  } catch (err) {
    return json(
      res,
      502,
      {
        detail:
          upstream.mode === "bridge"
            ? "0DTE bridge unreachable — SPY-DER state is read through that host until a direct tunnel exists"
            : "spy-der-dashboard-api unreachable — check SPY_DER_DASHBOARD_URL and the VPS tunnel",
        error: err instanceof Error ? err.message : String(err),
        mode: upstream.mode,
      },
      upstream.mode
    );
  }
}
