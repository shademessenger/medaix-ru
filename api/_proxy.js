const UPSTREAM = "https://207.90.238.174/ai/api";

function buildHeaders(extra = {}) {
  return {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    ...extra,
  };
}

export async function proxyJson(req, res, targetPath) {
  const url = new URL(UPSTREAM + targetPath);
  if (req.method === "GET") {
    for (const [key, value] of Object.entries(req.query || {})) {
      if (Array.isArray(value)) {
        value.forEach((item) => url.searchParams.append(key, item));
      } else if (value !== undefined) {
        url.searchParams.set(key, value);
      }
    }
  }

  const upstream = await fetch(url, {
    method: req.method,
    headers: buildHeaders(),
    body: req.method === "GET" || req.method === "HEAD" ? undefined : JSON.stringify(req.body || {}),
  });

  const raw = await upstream.text();
  console.log("[proxy]", req.method, targetPath, upstream.status);
  console.log("[proxy] raw body", raw);

  res.status(upstream.status);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");

  try {
    res.send(raw ? JSON.parse(raw) : {});
  } catch (error) {
    res.send({
      error: "upstream returned non-json response",
      status: upstream.status,
      body: raw.slice(0, 500),
    });
  }
}
