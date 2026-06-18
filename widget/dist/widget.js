(function () {
  var cfg = window.GENCO_CONFIG || {};
  var script = document.currentScript;
  var BACKEND_URL = cfg.backendUrl || (script && script.dataset.backendUrl) || "http://localhost:8000";
  var PRIMARY = cfg.primaryColor || "#2e7d32";   // TODO: real GC brand color
  var LOGO = cfg.logoUrl || "";                   // TODO: real GC logo URL
  var KEY = "genco_session_id";

  var css = "" +
    ".gc-launch{position:fixed;right:20px;bottom:20px;width:60px;height:60px;border-radius:50%;" +
    "background:" + PRIMARY + ";color:#fff;border:0;font-size:26px;cursor:pointer;z-index:2147483000;box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    ".gc-panel{position:fixed;right:20px;bottom:90px;width:380px;max-width:calc(100vw - 40px);height:560px;max-height:calc(100vh - 120px);" +
    "background:#fff;border-radius:14px;box-shadow:0 10px 40px rgba(0,0,0,.25);display:none;flex-direction:column;overflow:hidden;z-index:2147483000;font-family:system-ui,sans-serif}" +
    ".gc-panel.open{display:flex}" +
    ".gc-head{background:" + PRIMARY + ";color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;font-weight:600}" +
    ".gc-head img{height:24px}.gc-close{margin-left:auto;background:none;border:0;color:#fff;font-size:20px;cursor:pointer}" +
    ".gc-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}" +
    ".gc-b{max-width:80%;padding:9px 12px;border-radius:12px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}" +
    ".gc-user{align-self:flex-end;background:" + PRIMARY + ";color:#fff}" +
    ".gc-bot{align-self:flex-start;background:#f0f0f0;color:#111}" +
    ".gc-qr{display:flex;flex-wrap:wrap;gap:8px}.gc-qr button{border:1px solid " + PRIMARY + ";color:" + PRIMARY + ";background:#fff;border-radius:16px;padding:7px 12px;cursor:pointer}" +
    ".gc-input{display:flex;border-top:1px solid #eee}.gc-input input{flex:1;border:0;padding:14px;font-size:14px;outline:none}" +
    ".gc-input button{border:0;background:" + PRIMARY + ";color:#fff;padding:0 18px;cursor:pointer}" +
    ".gc-typing{align-self:flex-start;color:#888;font-style:italic;padding:4px 12px}" +
    "@media(max-width:480px){.gc-panel{right:0;bottom:0;width:100vw;height:100vh;max-width:100vw;max-height:100vh;border-radius:0}}";
  var style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);

  var launch = document.createElement("button");
  launch.className = "gc-launch"; launch.textContent = "💬"; launch.setAttribute("aria-label", "Open chat");
  var panel = document.createElement("div"); panel.className = "gc-panel";
  panel.innerHTML =
    '<div class="gc-head">' + (LOGO ? '<img src="' + LOGO + '" alt="">' : "") +
    '<span>Generation Conscious</span><button class="gc-close" aria-label="Close">×</button></div>' +
    '<div class="gc-msgs"></div>' +
    '<div class="gc-input"><input type="text" placeholder="Type a message…"><button>Send</button></div>';
  document.body.appendChild(launch); document.body.appendChild(panel);

  var msgs = panel.querySelector(".gc-msgs");
  var input = panel.querySelector("input");
  var sessionId = localStorage.getItem(KEY);
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
      .then(function (r) { return r.json(); })
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
    }).then(function (r) { return r.json(); }).then(function (data) {
      typing.remove();
      if (data.session_id) { sessionId = data.session_id; localStorage.setItem(KEY, sessionId); }
      bubble("bot", data.reply || "");
    }).catch(function () {
      typing.remove();
      bubble("bot", "I'm having trouble reaching the team right now — please email Info@GenerationConscious.co.");
    });
  }

  function open() {
    panel.classList.add("open");
    if (!greeted) { greeted = true; if (sessionId) loadHistory(); else greet(); }
  }
  launch.onclick = open;
  panel.querySelector(".gc-close").onclick = function () { panel.classList.remove("open"); };
  panel.querySelector(".gc-input button").onclick = function () { send(input.value); };
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") send(input.value); });
})();
