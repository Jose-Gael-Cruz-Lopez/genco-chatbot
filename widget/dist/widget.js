(function () {
  /* ── CONFIG ─────────────────────────────────────────────────────────────
   * Branding below was derived from the live site 2026-08 — confirm with GC
   * team before go-live (README runbook step 3):
   *   PRIMARY  #FF0719  = Elementor global color "f55a23f" (kit-5), the red
   *            used for nav/buttons site-wide; identical to the wordmark
   *            PNG's palette color.
   *   LOGO     the header wordmark the live homepage ships (300px variant).
   * Both can be overridden per-embed via window.GENCO_CONFIG
   * ({backendUrl, primaryColor, logoUrl}) or data-backend-url on the tag.
   * ──────────────────────────────────────────────────────────────────────── */
  var cfg = window.GENCO_CONFIG || {};
  var script = document.currentScript;
  var BACKEND_URL = cfg.backendUrl || (script && script.dataset.backendUrl) || "http://localhost:8000";
  var PRIMARY = cfg.primaryColor || "#FF0719"; // derived from live site 2026-08 — confirm with GC team
  var LOGO = cfg.logoUrl || "https://generationconscious.co/wp-content/uploads/2022/02/Droplet.g_Wordmark-300x106.png"; // derived from live site 2026-08 — confirm with GC team
  var KEY = "genco_session_id";
  var FRIENDLY_ERROR = "I'm having trouble reaching the team right now — please email Info@GenerationConscious.co.";

  /* localStorage can throw (Safari private mode, Chrome "Block all cookies");
   * degrade to a per-pageload session instead of dying mid-IIFE. */
  function storeGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* no-op */ } }

  var css = "" +
    ".gc-launch{position:fixed;right:20px;bottom:20px;width:60px;height:60px;border-radius:50%;" +
    "background:" + PRIMARY + ";color:#fff;border:0;font-size:26px;cursor:pointer;z-index:2147483000;box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    ".gc-panel{position:fixed;right:20px;bottom:90px;width:380px;max-width:calc(100vw - 40px);height:560px;max-height:calc(100vh - 120px);" +
    "background:#fff;border-radius:14px;box-shadow:0 10px 40px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden;z-index:2147483000;font-family:system-ui,sans-serif}" +
    ".gc-panel.open{display:flex}" +
    ".gc-head{background:" + PRIMARY + ";color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;font-weight:600}" +
    /* logo is red-on-transparent; white chip keeps it legible on the red header */
    ".gc-head img{height:22px;background:#fff;border-radius:5px;padding:2px 6px}" +
    ".gc-close{margin-left:auto;background:none;border:0;color:#fff;font-size:20px;cursor:pointer}" +
    ".gc-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}" +
    ".gc-b{max-width:80%;padding:9px 12px;border-radius:12px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}" +
    ".gc-user{align-self:flex-end;background:" + PRIMARY + ";color:#fff}" +
    ".gc-bot{align-self:flex-start;background:#f0f0f0;color:#111}" +
    ".gc-qr{display:flex;flex-wrap:wrap;gap:8px}.gc-qr button{border:1px solid " + PRIMARY + ";color:" + PRIMARY + ";background:#fff;border-radius:16px;padding:7px 12px;cursor:pointer}" +
    ".gc-input{display:flex;border-top:1px solid #eee}.gc-input input{flex:1;border:0;padding:14px;font-size:14px;outline:none}" +
    ".gc-input button{border:0;background:" + PRIMARY + ";color:#fff;padding:0 18px;cursor:pointer}" +
    ".gc-typing{align-self:flex-start;color:#888;font-style:italic;padding:4px 12px}" +
    /* 100dvh (with 100vh fallback) so the iOS toolbar doesn't cover the input;
     * safe-area padding keeps the input row above the home indicator. */
    "@media(max-width:480px){.gc-panel{right:0;bottom:0;width:100vw;height:100vh;height:100dvh;max-width:100vw;max-height:100vh;max-height:100dvh;border-radius:0}" +
    ".gc-input{padding-bottom:env(safe-area-inset-bottom)}}";
  var style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);

  var launch = document.createElement("button");
  launch.className = "gc-launch"; launch.textContent = "💬"; launch.setAttribute("aria-label", "Open chat");

  /* Panel is built with DOM APIs (no innerHTML) so cfg-supplied values like
   * LOGO are never interpolated into markup. */
  var panel = document.createElement("div");
  panel.className = "gc-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", "Generation Conscious chat");

  var head = document.createElement("div"); head.className = "gc-head";
  if (LOGO) {
    var logoImg = document.createElement("img");
    logoImg.src = LOGO; logoImg.alt = "";
    head.appendChild(logoImg);
  }
  var title = document.createElement("span"); title.textContent = "Generation Conscious";
  head.appendChild(title);
  var closeBtn = document.createElement("button");
  closeBtn.className = "gc-close"; closeBtn.textContent = "×"; closeBtn.setAttribute("aria-label", "Close");
  head.appendChild(closeBtn);

  var msgs = document.createElement("div");
  msgs.className = "gc-msgs";
  msgs.setAttribute("role", "log");
  msgs.setAttribute("aria-live", "polite");

  var inputRow = document.createElement("div"); inputRow.className = "gc-input";
  var input = document.createElement("input");
  input.type = "text"; input.placeholder = "Type a message…"; input.setAttribute("aria-label", "Type a message");
  var sendBtn = document.createElement("button"); sendBtn.textContent = "Send";
  inputRow.appendChild(input); inputRow.appendChild(sendBtn);

  panel.appendChild(head); panel.appendChild(msgs); panel.appendChild(inputRow);
  document.body.appendChild(launch); document.body.appendChild(panel);

  var sessionId = storeGet(KEY);
  var greeted = false;

  function bubble(role, text) {
    var d = document.createElement("div");
    d.className = "gc-b " + (role === "user" ? "gc-user" : "gc-bot");
    d.textContent = text; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
  }
  function greet() {
    bubble("bot", "How can we support your sustainability journey?");
    var qr = document.createElement("div"); qr.className = "gc-qr";
    ["Buy Sheets", "Buy Refill Stations", "Question for the team"].forEach(function (label) {
      var b = document.createElement("button"); b.textContent = label;
      b.onclick = function () { qr.remove(); send(label); };
      qr.appendChild(b);
    });
    msgs.appendChild(qr);
  }
  function loadHistory() {
    fetch(BACKEND_URL + "/history?session_id=" + encodeURIComponent(sessionId))
      .then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); })
      .then(function (data) {
        if (data.messages && data.messages.length) {
          data.messages.forEach(function (m) { bubble(m.role, m.content); });
        } else { greet(); }
      }).catch(function () { greet(); });
  }
  function send(text) {
    if (!text.trim()) return;
    bubble("user", text); input.value = "";
    var typing = document.createElement("div"); typing.className = "gc-typing"; typing.textContent = "…";
    msgs.appendChild(typing); msgs.scrollTop = msgs.scrollHeight;
    fetch(BACKEND_URL + "/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text })
    }).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
      typing.remove();
      if (data.session_id) { sessionId = data.session_id; storeSet(KEY, sessionId); }
      bubble("bot", data.reply || FRIENDLY_ERROR);
    }).catch(function () {
      typing.remove();
      bubble("bot", FRIENDLY_ERROR);
    });
  }

  /* Scroll-lock the host page behind the full-screen mobile panel; restore
   * whatever inline overflow the host had. */
  var scrollLocked = false;
  var prevOverflow = "";
  function lockScroll() {
    if (!scrollLocked && window.matchMedia && window.matchMedia("(max-width:480px)").matches) {
      prevOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      scrollLocked = true;
    }
  }
  function unlockScroll() {
    if (scrollLocked) { document.body.style.overflow = prevOverflow; scrollLocked = false; }
  }

  function open() {
    panel.classList.add("open");
    lockScroll();
    if (!greeted) { greeted = true; if (sessionId) loadHistory(); else greet(); }
    input.focus();
  }
  function close() {
    panel.classList.remove("open");
    unlockScroll();
    launch.focus();
  }

  launch.onclick = open;
  closeBtn.onclick = close;
  sendBtn.onclick = function () { send(input.value); };
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") send(input.value); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && panel.classList.contains("open")) close();
  });
})();
