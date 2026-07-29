/**
 * Vercel serverless proxy — forwards /api/* to spy-der-dashboard-api on the VPS.
 *
 * Set SPY_DER_DASHBOARD_URL in the Vercel project env (required).
 * Set DASHBOARD_TOKEN only when the upstream hop requires Bearer auth
 * (e.g. temporary routing through the 0DTE dashboard).
 *
 * Browser calls look like /api/v1/system because the static shell sets
 * data-spy-der-base="/api". This handler strips /api and forwards to
 * ${SPY_DER_DASHBOARD_URL}/v1/system.
 *
 * GET/HEAD stay read-only. POST is allowed only for operator knob writes
 * (promote / reject / rollback). The browser's operator secret travels as
 * X-Spy-Der-Operator-Token so Authorization can carry DASHBOARD_TOKEN.
 */
export default async function handler(req, res) {
  const requestUrl = new URL(req.url, "http://localhost");
  // Derive subpath from the raw URL — req.query.path is not reliable for
  // catch-all functions on Vercel (observed empty in production).
  const subpath = requestUrl.pathname.replace(/^\/api\/?/, "");
  const operatorWrite =
    req.method === "POST" &&
    /^(v1\/dojo\/(promote|reject|rollback))\/?$/.test(subpath);

  if (!["GET", "HEAD", "OPTIONS"].includes(req.method) && !operatorWrite) {
    return res
      .status(405)
      .json({ detail: "Method not allowed — read-only dashboard" });
  }

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  const dashboardBase = (
    process.env.SPY_DER_DASHBOARD_URL ||
    process.env.VPS_API_URL ||
    ""
  ).replace(/\/$/, "");
  const token = (process.env.DASHBOARD_TOKEN || "").trim();

  if (!dashboardBase) {
    return res.status(503).json({
      detail:
        "Vercel env not configured — set SPY_DER_DASHBOARD_URL to the reachable spy-der-dashboard-api base",
    });
  }

  const qsStr = requestUrl.searchParams.toString();
  const target = `${dashboardBase}/${subpath}${qsStr ? `?${qsStr}` : ""}`;

  const headers = {
    Accept: req.headers.accept || "application/json",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const operator =
    req.headers["x-spy-der-operator-token"] ||
    req.headers["X-Spy-Der-Operator-Token"];
  if (operator) {
    headers["X-Spy-Der-Operator-Token"] = operator;
  }

  let body;
  if (req.method === "POST") {
    headers["Content-Type"] = req.headers["content-type"] || "application/json";
    if (typeof req.body === "string") body = req.body;
    else if (typeof Buffer !== "undefined" && Buffer.isBuffer(req.body))
      body = req.body;
    else body = JSON.stringify(req.body ?? {});
  }

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
    });

    const contentType =
      upstream.headers.get("content-type") || "application/json";
    res.status(upstream.status);
    res.setHeader("Content-Type", contentType);
    res.setHeader("Cache-Control", "no-store");

    const responseText = await upstream.text();
    return res.send(responseText);
  } catch (err) {
    return res.status(502).json({
      detail:
        "spy-der-dashboard-api unreachable — check SPY_DER_DASHBOARD_URL and the VPS tunnel",
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
