// Bollisoft-Hauptnavigation zum Einbinden in eigenstaendige Projekte
// (Drohnen Tools, CoverStore/Maker): <script src="/site-nav.js" defer></script>
// Die Leiste steckt in einem Shadow DOM, damit sich ihre Styles und die der
// jeweiligen Seite nicht gegenseitig beeinflussen.
{{ nav_js }}
(() => {
  if (document.querySelector("bollisoft-nav")) {
    return;
  }
  const host = document.createElement("bollisoft-nav");
  const root = host.attachShadow({ mode: "open" });
  // CSS direkt eingebettet, damit die Leiste sofort fertig gestylt erscheint.
  const style = document.createElement("style");
  style.textContent = {{ nav_css|tojson }};
  const markup = document.createElement("template");
  markup.innerHTML = {{ markup|tojson }};
  root.append(style, markup.content);

  // Seitenabstaende des Body ausgleichen, damit die Leiste ueber die volle
  // Breite am oberen Rand sitzt; der obere Abstand kommt unter die Leiste.
  const bodyStyle = window.getComputedStyle(document.body);
  host.style.marginTop = "-" + bodyStyle.paddingTop;
  host.style.marginBottom = bodyStyle.paddingTop;
  host.style.marginLeft = "-" + bodyStyle.paddingLeft;
  host.style.marginRight = "-" + bodyStyle.paddingRight;

  document.body.prepend(host);
  window.initSiteNav(root.querySelector("[data-site-nav]"));

  // Favicon der Webseite, wenn die Seite kein eigenes mitbringt
  if (!document.querySelector('link[rel~="icon"]')) {
    const icons = [
      ["icon", "image/svg+xml", "", {{ url_for('static', filename='favicon.svg')|tojson }}],
      ["icon", "image/png", "32x32", {{ url_for('static', filename='favicon-32.png')|tojson }}],
      ["icon", "image/png", "16x16", {{ url_for('static', filename='favicon-16.png')|tojson }}],
      ["apple-touch-icon", "", "", {{ url_for('static', filename='apple-touch-icon.png')|tojson }}],
    ];
    for (const [rel, type, sizes, href] of icons) {
      const link = document.createElement("link");
      link.rel = rel;
      if (type) link.type = type;
      if (sizes) link.sizes = sizes;
      link.href = href;
      document.head.append(link);
    }
  }

  // Kleiner Admin-Link unten rechts wie auf den Seiten der Webseite (base.html)
  if (!document.querySelector(".admin-corner-link, bollisoft-admin")) {
    const adminHost = document.createElement("bollisoft-admin");
    const adminRoot = adminHost.attachShadow({ mode: "open" });
    const adminStyle = document.createElement("style");
    adminStyle.textContent =
      "a{position:fixed;right:10px;bottom:8px;z-index:2147483000;width:22px;height:22px;line-height:22px;" +
      "text-align:center;border-radius:50%;font:14px/22px system-ui,sans-serif;color:#9aaba5;opacity:.35;" +
      "text-decoration:none;transition:opacity .15s,background-color .15s}" +
      "a:hover,a:focus-visible{opacity:1;background:rgba(127,127,127,.18);outline:none}";
    const adminLink = document.createElement("a");
    adminLink.href = {{ url_for('admin_dashboard')|tojson }};
    adminLink.title = "Admin";
    adminLink.setAttribute("aria-label", {{ _("Admin-Bereich")|tojson }});
    adminLink.textContent = "⚙";
    adminRoot.append(adminStyle, adminLink);
    document.body.append(adminHost);
  }
})();
