/**
 * Vercel serverless proxy — forwards /api/* to the VPS dashboard.
 * Set VPS_API_URL and DASHBOARD_TOKEN in Vercel project environment variables.
 *
 * GET/HEAD stay read-only. POST is allowed only for SPY-DER operator knob
 * writes (promote / reject / rollback). The browser's operator secret travels
 * as X-Spy-Der-Operator-Token because Authorization is reserved for
 * DASHBOARD_TOKEN on this hop.
 */
export default async function handler(req, res) {
  const requestUrl = new URL(req.url, "http://localhost");
  const subpath = requestUrl.pathname.replace(/^\/api\/?/, "");
  const operatorWrite =
    req.method === "POST" &&
    /^(spy-der\/v1\/dojo\/(promote|reject|rollback))\/?$/.test(subpath);

  if (!["GET", "HEAD", "OPTIONS"].includes(req.method) && !operatorWrite) {
    return res.status(405).json({ detail: "Method not allowed — read-only dashboard" });
  }

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  const vpsBase = process.env.VPS_API_URL?.replace(/\/$/, "");
  const token = process.env.DASHBOARD_TOKEN;

  if (!vpsBase || !token) {
    return res.status(503).json({
      detail: "Vercel env not configured — set VPS_API_URL and DASHBOARD_TOKEN",
    });
  }

  const qsStr = requestUrl.searchParams.toString();
  const target = `${vpsBase}/api/${subpath}${qsStr ? `?${qsStr}` : ""}`;

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: req.headers.accept || "application/json",
  };
  const operator =
    req.headers["x-spy-der-operator-token"] ||
    req.headers["X-Spy-Der-Operator-Token"];
  if (operator) {
    headers["X-Spy-Der-Operator-Token"] = operator;
  }
  if (req.method === "POST") {
    headers["Content-Type"] = req.headers["content-type"] || "application/json";
  }

  let body;
  if (req.method === "POST") {
    if (typeof req.body === "string") body = req.body;
    else if (Buffer.isBuffer(req.body)) body = req.body;
    else body = JSON.stringify(req.body ?? {});
  }

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
    });

    const contentType = upstream.headers.get("content-type") || "application/json";
    res.status(upstream.status);
    res.setHeader("Content-Type", contentType);
    res.setHeader("Cache-Control", "no-store");

    const body = await upstream.text();
    return res.send(body);
  } catch (err) {
    return res.status(502).json({
      detail: "VPS dashboard unreachable — check VPS_API_URL and tunnel",
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
