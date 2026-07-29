/**
 * Vercel serverless proxy — forwards /api/* to the VPS dashboard.
 * Set VPS_API_URL and DASHBOARD_TOKEN in Vercel project environment variables.
 * The browser never needs to paste DASHBOARD_TOKEN; Vercel adds it server-side.
 *
 * GET/HEAD stay read-only. POST is allowed only for SPY-DER operator knob
 * writes (promote / reject / rollback) via X-Spy-Der-Operator-Token.
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

  let requestBody;
  if (req.method === "POST") {
    headers["Content-Type"] = req.headers["content-type"] || "application/json";
    if (typeof req.body === "string") requestBody = req.body;
    else if (typeof Buffer !== "undefined" && Buffer.isBuffer(req.body)) requestBody = req.body;
    else requestBody = JSON.stringify(req.body ?? {});
  }

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: requestBody,
    });

    const contentType = upstream.headers.get("content-type") || "application/json";
    res.status(upstream.status);
    res.setHeader("Content-Type", contentType);
    res.setHeader("Cache-Control", "no-store");

    const responseText = await upstream.text();
    return res.send(responseText);
  } catch (err) {
    return res.status(502).json({
      detail: "VPS dashboard unreachable — check VPS_API_URL and tunnel",
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
