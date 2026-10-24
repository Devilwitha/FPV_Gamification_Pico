// Hauptnavigation (templates/_nav.html): mobiles Menue, Dropdowns und
// Markierung der aktuellen Seite. Wird ueber /site-nav.js auch in den
// eigenstaendigen Projekten (Drohnen Tools, CoverStore/Maker) genutzt.
window.initSiteNav = (header) => {
  const toggle = header.querySelector(".nav-toggle");
  const groups = [...header.querySelectorAll(".nav-group")];
  const path = window.location.pathname;

  header.querySelectorAll(".nav-item[href]:not([data-nav-ignore]), .nav-link[href]").forEach((item) => {
    const target = new URL(item.href, window.location.href).pathname;
    // Direkte Links (.nav-link, z. B. Forum) gelten auch fuer ihre Unterseiten.
    const isSection = target.endsWith("/") || item.classList.contains("nav-link");
    const isCurrent = isSection ? path === target || path.startsWith(target.replace(/\/?$/, "/")) : path === target;
    if (!isCurrent) {
      return;
    }
    item.setAttribute("aria-current", "page");
    if (item.classList.contains("nav-link")) {
      item.classList.add("is-active");
    }
    const group = item.closest(".nav-group");
    if (group) {
      group.querySelector(".nav-trigger").classList.add("is-active");
    }
  });

  // Sprachwahl (/sprache/<code>): danach zur aktuellen Seite zurueck - auch
  // in den eingebetteten Projekten, deren Navigation unter /site-nav.js entsteht.
  header.querySelectorAll("[data-lang]").forEach((link) => {
    const next = window.location.pathname + window.location.search + window.location.hash;
    link.href = "/sprache/" + link.dataset.lang + "?next=" + encodeURIComponent(next);
  });

  const closeGroups = (except) => {
    groups.forEach((group) => {
      if (group !== except) {
        group.open = false;
      }
    });
  };

  groups.forEach((group) => {
    group.addEventListener("toggle", () => {
      if (group.open) {
        closeGroups(group);
      }
    });
  });

  if (toggle) {
    toggle.addEventListener("click", () => {
      const isOpen = header.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
      toggle.setAttribute("aria-label", isOpen ? toggle.dataset.labelClose || "Menü schließen" : toggle.dataset.labelOpen || "Menü öffnen");
      if (!isOpen) {
        closeGroups(null);
      }
    });
  }

  // composedPath() statt contains(), damit Klicks auch funktionieren, wenn
  // die Navigation in einem Shadow DOM steckt (siehe /site-nav.js).
  document.addEventListener("click", (event) => {
    if (!event.composedPath().includes(header)) {
      closeGroups(null);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    const openGroup = groups.find((group) => group.open);
    if (openGroup) {
      openGroup.open = false;
      openGroup.querySelector(".nav-trigger").focus();
    }
  });
};

document.querySelectorAll("[data-site-nav]").forEach((header) => window.initSiteNav(header));
