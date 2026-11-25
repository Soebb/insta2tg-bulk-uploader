/**
 * Authenticated streaming relay for Instagram CDN media.
 *
 * Required Worker secret: CDN_PROXY_SECRET (minimum 32 characters).
 * Android signs: HMAC-SHA256(secret, "<expiry>\n<source-url>").
 *
 * Supported endpoints:
 *   GET  /health
 *   GET  /relay?u=<encoded-https-url>&e=<unix-expiry>&s=<hex-hmac>
 *   HEAD /relay?u=<encoded-https-url>&e=<unix-expiry>&s=<hex-hmac>
 *
 * Only scontent*.cdninstagram.com source hosts are accepted.
 */
const encoder = new TextEncoder();

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function hexToBytes(value) {
  if (!/^[0-9a-f]{64}$/i.test(value)) return null;
  const bytes = new Uint8Array(32);
  for (let i = 0; i < 32; i++) bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

function allowedInstagramCdn(url) {
  return url.protocol === "https:" &&
    url.username === "" && url.password === "" &&
    url.port === "" &&
    /^scontent[a-z0-9-]*\.cdninstagram\.com$/i.test(url.hostname);
}

async function validSignature(secret, expiry, sourceUrl, suppliedHex) {
  const signature = hexToBytes(suppliedHex);
  if (!signature) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify(
    "HMAC",
    key,
    signature,
    encoder.encode(expiry + "\n" + sourceUrl),
  );
}

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    console.log(JSON.stringify({ event: "request_received", method: request.method, path: incoming.pathname }));
    if (incoming.pathname === "/health" && request.method === "GET") {
      return json({ ok: true, service: "instagram-cdn-relay" });
    }
    if (incoming.pathname !== "/relay") return new Response("Not found", { status: 404 });
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD" } });
    }
    if (!env.CDN_PROXY_SECRET || env.CDN_PROXY_SECRET.length < 32) {
      return new Response("Worker secret is not configured", { status: 503 });
    }

    const source = incoming.searchParams.get("u") || "";
    const expiry = incoming.searchParams.get("e") || "";
    const signature = incoming.searchParams.get("s") || "";
    if (!source || !/^\d{10}$/.test(expiry) || !signature) {
      return new Response("Missing or invalid signed URL parameters", { status: 400 });
    }

    const expiryNumber = Number(expiry);
    const now = Math.floor(Date.now() / 1000);
    if (expiryNumber < now || expiryNumber > now + 660) {
      return new Response("Signed URL expired or expiry is too far in the future", { status: 403 });
    }

    let target;
    try {
      target = new URL(source);
    } catch (_) {
      return new Response("Invalid source URL", { status: 400 });
    }
    if (!allowedInstagramCdn(target)) {
      console.log(JSON.stringify({ event: "request_rejected", reason: "disallowed_source_host", source_host: target.hostname }));
      return new Response("Source host is not an allowed Instagram CDN host", { status: 403 });
    }
    console.log(JSON.stringify({ event: "source_validated", source_host: target.hostname, method: request.method }));

    let authorized = false;
    try {
      authorized = await validSignature(env.CDN_PROXY_SECRET, expiry, source, signature);
    } catch (_) {
      authorized = false;
    }
    if (!authorized) {
      console.log(JSON.stringify({ event: "request_rejected", reason: "invalid_signature", source_host: target.hostname }));
      return new Response("Invalid signature", { status: 403 });
    }
    console.log(JSON.stringify({ event: "signature_valid", source_host: target.hostname }));

    const upstreamHeaders = new Headers({
      "accept": "*/*",
      "accept-language": "en-US,en;q=0.9",
      "referer": "https://www.instagram.com/",
      "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "accept-encoding": "identity",
    });
    for (const name of ["range", "if-range"]) {
      const value = request.headers.get(name);
      if (value) upstreamHeaders.set(name, value);
    }

    let upstream;
    try {
      upstream = await fetch(target.toString(), {
        method: request.method,
        headers: upstreamHeaders,
        redirect: "follow",
      });
    } catch (error) {
      console.log(JSON.stringify({ event: "upstream_fetch_failed", source_host: target.hostname, error: String(error) }));
      return new Response("Could not reach Instagram CDN", { status: 502 });
    }
    console.log(JSON.stringify({
      event: "upstream_response",
      source_host: target.hostname,
      status: upstream.status,
      content_type: upstream.headers.get("content-type"),
      content_length: upstream.headers.get("content-length"),
      content_range: upstream.headers.get("content-range"),
      final_host: (() => { try { return new URL(upstream.url || target.toString()).hostname; } catch (_) { return "invalid"; } })()
    }));

    // Do not let an upstream redirect turn this endpoint into a general proxy.
    let finalUrl;
    try {
      finalUrl = new URL(upstream.url || target.toString());
    } catch (_) {
      return new Response("Invalid upstream response URL", { status: 502 });
    }
    if (!allowedInstagramCdn(finalUrl)) {
      return new Response("Instagram CDN redirected to a disallowed host", { status: 502 });
    }

    const responseHeaders = new Headers();
    for (const name of [
      "content-type",
      "content-length",
      "content-range",
      "accept-ranges",
      "last-modified",
      "etag",
    ]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    if (!responseHeaders.has("content-type")) {
      responseHeaders.set("content-type", "video/mp4");
    }
    if (!responseHeaders.has("content-disposition")) {
      responseHeaders.set("content-disposition", 'inline; filename="instagram-video.mp4"');
    }
    responseHeaders.set("cache-control", "private, no-store");
    responseHeaders.set("x-content-type-options", "nosniff");

    console.log(JSON.stringify({ event: "relay_response_returned", status: upstream.status, source_host: target.hostname }));
    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  },
};
