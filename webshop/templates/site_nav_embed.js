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
})();
