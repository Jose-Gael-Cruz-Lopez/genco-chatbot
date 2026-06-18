# Launch Checklist

Work through these items in order before going live. Check each box once verified.

---

## Embed

- [ ] Widget script tag is live on the Generation Conscious WordPress site.
  Paste the following into a header/footer plugin or Elementor custom-code block:
  ```html
  <script src="https://YOUR-BACKEND-HOST/widget/widget.js"
          data-backend-url="https://YOUR-BACKEND-HOST"></script>
  ```
  Replace `YOUR-BACKEND-HOST` with the actual Render (or Railway/Fly) service URL.
- [ ] Widget JS is reachable in production: `curl -I <embed src URL>` returns HTTP 200.
  The default Dockerfile now bundles `widget/dist/` (build context = repo root), so this should
  pass out of the box; this check guards against a regression or a misconfigured CDN.

---

## Security & CORS

- [ ] `ALLOWED_ORIGINS` is locked to `https://generationconscious.co` in the production
  environment (remove `http://localhost:5500` and any dev origins before go-live).
- [ ] Rate limiting is keyed on client IP (`X-Forwarded-For`). Confirm Render forwards the real
  client IP (it sets `X-Forwarded-For` by default) so `RATE_LIMIT_PER_MINUTE` is enforced per IP,
  not per browser-supplied session. The daily cost cap is the global backstop.

---

## Knowledge Base

- [ ] `STORE_URL` in the KB is confirmed as the home-delivery product page:
  `https://generationconscious.co/product/laundry-detergent-sheets/`
  (already set — verify no accidental edits).

---

## Lead Routing

- [ ] Escalation email verified end-to-end: send a test wholesale inquiry and confirm
  `Info@GenerationConscious.co` receives the notification.
- [ ] Lead capture verified for the **wholesale** flow (Name, Email, Phone, Organization,
  Estimated Sheets → Supabase row + email + Pipedrive).
- [ ] Lead capture verified for the **refill-station** flow (Name, Email, Phone, Organization,
  Laundry Rooms, Students/Tenants → Supabase row + email + Pipedrive).
- [ ] Pipedrive person and deal created correctly for both flows.

---

## Reliability

- [ ] `DAILY_COST_CAP_USD` is set to a value appropriate for expected traffic (e.g., `10.0`).
  Confirm the cost-cap path returns the static unavailable message (it does NOT call the fallback
  model). The fallback model (`OPENROUTER_MODEL_FALLBACK`) triggers only on primary model failure.

---

## Observability

- [ ] LangFuse is receiving traces: open cloud.langfuse.com, send a test chat message, and
  confirm a trace appears with retrieve/generate/respond spans.

---

## QA

- [ ] Mobile QA: widget opens, sends a message, displays a reply on iOS and Android.
- [ ] Desktop QA: widget opens, sends a message, displays a reply in Chrome and Safari/Firefox.

---

All boxes checked = cleared for launch.
