/* Mirage embed widget. Usage:
 * <script src="widget.js" data-mirage-host="https://api.example.com"
 *         data-conversation="c_xxx" data-api-key="mk_..." data-width="360" data-height="520"></script>
 * WARNING: data-api-key is visible to page visitors. Use a low-credit key dedicated to embedding. */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var host = (s.getAttribute("data-mirage-host") || new URL(s.src).origin).replace(/\/$/, "");
  var cid = s.getAttribute("data-conversation");
  var key = s.getAttribute("data-api-key");
  if (!cid || !key) { console.error("[mirage] data-conversation and data-api-key are required"); return; }
  var f = document.createElement("iframe");
  f.src = host + "/v1/playground?cid=" + encodeURIComponent(cid) + "&api_key=" + encodeURIComponent(key);
  f.allow = "microphone; camera; autoplay";
  f.title = "Mirage conversation";
  f.style.cssText = "border:0;border-radius:12px;width:" + (s.getAttribute("data-width") || "360") +
    "px;height:" + (s.getAttribute("data-height") || "520") + "px;max-width:100%";
  var target = s.getAttribute("data-target");
  var el = target && document.querySelector(target);
  if (el) el.appendChild(f); else s.parentNode.insertBefore(f, s.nextSibling);
})();
