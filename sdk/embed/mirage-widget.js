/*! Mirage widget v2 - one script tag, no API key. https://github.com/ (docs/WIDGET.md)
 * <script src="https://API/widget.js" data-token="sh_..." data-label="Talk to us" data-color="#6d5efc"
 *         data-position="bottom-right" data-greeting="Questions? Ask our AI" data-language="en" async></script> */
(function () {
  "use strict";
  var s = document.currentScript;
  if (!s || window.__mirageWidget) return;
  var token = s.getAttribute("data-token");
  if (!token) { console.error("[mirage] data-token is required"); return; }
  var host = (s.getAttribute("data-host") || new URL(s.src, location.href).origin).replace(/\/$/, "");
  var label = s.getAttribute("data-label") || "Talk to us";
  var color = /^#[0-9a-f]{6}$/i.test(s.getAttribute("data-color") || "") ? s.getAttribute("data-color") : "#6d5efc";
  var left = s.getAttribute("data-position") === "bottom-left";
  var teaser = s.getAttribute("data-greeting") || "";
  var lang = s.getAttribute("data-language") || "";
  var rgb = [1, 3, 5].map(function (i) { return parseInt(color.substr(i, 2), 16); });
  var fg = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000 > 150 ? "#111" : "#fff";
  var side = left ? "left" : "right";
  var h = document.createElement("div");
  h.setAttribute("data-mirage-widget", "");
  var r = h.attachShadow({ mode: "open" });
  r.innerHTML =
    "<style>*{box-sizing:border-box}:host{all:initial}" +
    ".b{position:fixed;bottom:20px;" + side + ":20px;z-index:2147483000;display:flex;align-items:center;gap:8px;border:0;border-radius:999px;" +
    "padding:14px 20px;font:600 15px/1 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;cursor:pointer;background:" + color + ";color:" + fg +
    ";box-shadow:0 8px 28px rgba(0,0,0,.28);transition:transform .15s}.b:hover{transform:translateY(-2px)}" +
    ".b:focus-visible,.x:focus-visible{outline:3px solid #fff;box-shadow:0 0 0 5px #1a73e8}" +
    ".t{position:fixed;bottom:84px;" + side + ":20px;z-index:2147483000;max-width:260px;background:#fff;color:#1a1a1a;padding:12px 30px 12px 14px;" +
    "border-radius:14px;font:14px/1.35 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.22)}.t button{position:absolute;top:2px;right:4px;" +
    "border:0;background:none;font-size:18px;cursor:pointer;color:#555;padding:6px}" +
    ".p{position:fixed;bottom:20px;" + side + ":20px;z-index:2147483001;width:400px;height:min(680px,calc(100vh - 40px));display:none;flex-direction:column;" +
    "background:#0b0a10;border-radius:16px;overflow:hidden;box-shadow:0 16px 60px rgba(0,0,0,.5)}.p.o{display:flex}" +
    ".h{display:flex;align-items:center;justify-content:space-between;padding:8px 8px 8px 16px;background:" + color + ";color:" + fg +
    ";font:600 14px system-ui,sans-serif}.x{border:0;background:none;color:inherit;font-size:24px;line-height:1;cursor:pointer;padding:6px 10px;border-radius:8px}" +
    "iframe{flex:1;border:0;width:100%;background:#0b0a10}.s{height:0;overflow:hidden}" +
    "@media(max-width:520px){.p{inset:0;width:100%;height:100%;border-radius:0}.t{max-width:calc(100vw - 40px)}}" +
    "@media(prefers-reduced-motion:reduce){.b{transition:none}}</style>" +
    '<button class="b" type="button" aria-haspopup="dialog" aria-expanded="false"></button>' +
    '<div class="t" role="note" hidden><span></span><button type="button" aria-label="Dismiss">&times;</button></div>' +
    '<div class="p" role="dialog" aria-modal="true"><div class="h"><span></span>' +
    '<button class="x" type="button" aria-label="Close">&times;</button></div><div class="s" tabindex="0"></div>' +
    '<iframe allow="microphone; camera; autoplay" title=""></iframe><div class="s" tabindex="0"></div></div>';
  var $ = function (q) { return r.querySelector(q); };
  var btn = $(".b"), tip = $(".t"), panel = $(".p"), fr = $("iframe"), close = $(".x"), sent = r.querySelectorAll(".s");
  btn.textContent = label;
  $(".h span").textContent = label;
  panel.setAttribute("aria-label", label);
  fr.title = label + " (AI conversation)";
  var shown = false;
  try { shown = sessionStorage.getItem("mirage-tip") === "1"; } catch (e) {}
  if (teaser && !shown) {
    $(".t span").textContent = teaser;
    setTimeout(function () { if (!panel.classList.contains("o")) tip.hidden = false; }, 1500);
  }
  function hideTip() { tip.hidden = true; try { sessionStorage.setItem("mirage-tip", "1"); } catch (e) {} }
  tip.querySelector("button").onclick = hideTip;
  var inerted = [];
  function open() {
    hideTip();
    if (!fr.src || fr.src === "about:blank" || fr.getAttribute("src") === "about:blank")
      fr.src = host + "/widget/frame/" + encodeURIComponent(token) + (lang ? "?lang=" + encodeURIComponent(lang) : "");
    panel.classList.add("o"); btn.setAttribute("aria-expanded", "true"); btn.style.visibility = "hidden";
    inerted = [].filter.call(document.body.children, function (e) { return e !== h && !e.inert; });
    inerted.forEach(function (e) { e.inert = true; });
    close.focus();
  }
  function shut() {
    if (!panel.classList.contains("o")) return;
    panel.classList.remove("o"); btn.setAttribute("aria-expanded", "false"); btn.style.visibility = "";
    fr.src = "about:blank"; // ends the conversation
    inerted.forEach(function (e) { e.inert = false; }); inerted = [];
    btn.focus();
  }
  btn.onclick = open; close.onclick = shut;
  sent[0].onfocus = function () { fr.focus(); };      // focus leaving the iframe backwards/forwards stays in the dialog
  sent[1].onfocus = function () { close.focus(); };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && panel.classList.contains("o")) { e.stopPropagation(); shut(); }
    else if (e.key === "Tab" && panel.classList.contains("o") && r.activeElement === close) { e.preventDefault(); (e.shiftKey ? sent[1] : fr).focus(); }
  }, true);
  window.addEventListener("message", function (e) {
    if (e.source === fr.contentWindow && e.data && e.data.mirage === "widget" && e.data.type === "close") shut();
  });
  window.__mirageWidget = { open: open, close: shut, element: h };
  (document.body ? Promise.resolve() : new Promise(function (ok) { document.addEventListener("DOMContentLoaded", ok); }))
    .then(function () { document.body.appendChild(h); });
})();
