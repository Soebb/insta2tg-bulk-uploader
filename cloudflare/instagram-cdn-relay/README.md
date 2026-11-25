# Instagram CDN relay (Cloudflare Worker)

This Worker is a small streaming bridge. Telegram requests the Worker URL; the Worker fetches the Instagram CDN URL with browser-like headers and streams the response body back. The Android phone never downloads the video bytes.

## Deploy from the Cloudflare dashboard

1. Open **Workers & Pages** in your Cloudflare dashboard.
2. Create a new Worker (for example, `instagram-cdn-relay`).
3. Open its editor and replace the sample code with the complete contents of `worker.js`.
4. Deploy the Worker.
5. Open the Worker **Settings → Variables and Secrets** section.
6. Add a secret named exactly `CDN_PROXY_SECRET`. Use a random value with at least 32 characters. Do not use a password you use elsewhere.
7. Save/deploy the Worker again.

You can generate a random secret on a computer with Python:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Configure the Android app

- **CDN proxy Worker URL:** the deployed Worker URL followed by `/relay`, for example `https://instagram-cdn-relay.<your-subdomain>.workers.dev/relay`.
- **CDN proxy shared secret:** the exact value stored in the Worker secret `CDN_PROXY_SECRET`.

Save settings. Check that opening the Worker base URL plus `/health` returns JSON containing `"ok":true`. The health endpoint does not expose the secret.

## Security and behavior

- Every media URL is signed with HMAC-SHA256 and expires after 10 minutes.
- Only HTTPS URLs on `scontent*.cdninstagram.com` are accepted; this is not a general-purpose open proxy.
- The Worker accepts GET and HEAD and forwards Range/If-Range headers.
- The response body is streamed; it is not stored by the Worker.
- Browser-like User-Agent and Instagram Referer headers are sent upstream. Instagram may still require additional headers or change its CDN behavior.
- Keep the Worker secret private. Anyone with both the secret and an unexpired signed URL can use that URL until it expires.
- This experiment only handles the single progressive video URL path currently selected by the Android engine. It does not change carousel or split audio/video handling.
