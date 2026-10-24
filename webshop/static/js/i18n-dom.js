// Mehrsprachigkeit fuer die Browser-Apps (Drohnen Tools, Blackbox Viewer).
// Einbinden moeglichst frueh im <head>:
//   <script src="/static/js/i18n-dom.js" data-bundle="tools"></script>
// Sprache: Cookie "lang" (gesetzt ueber das Sprachmenue der Webseite, i18n.py),
// sonst die Browsersprache. Bei Deutsch passiert nichts. Sonst wird
// /i18n/<bundle>/<lang>.json geladen ({"deutscher Text": "Uebersetzung"}; {0}, {1} …
// sind Platzhalter) und die Seite samt allem, was die App spaeter einblendet
// (MutationObserver), uebersetzt - Texte, placeholder/title/aria-label/alt,
// alert/confirm/prompt und - mit data-canvas am Skript-Tag - Canvas-Texte (HUD des
// Blackbox Viewers; nicht im Lua Studio, dessen Vorschau dem erzeugten Code entsprechen muss). Elemente mit data-no-i18n,
// translate="no" sowie code/pre/textarea bleiben unberuehrt.
// Fuer Code: window.I18N.t("Text {0}", wert).
(() => {
  "use strict";
  const LANGS = ["de", "en", "fr", "it", "es"];
  const script = document.currentScript;
  const bundle = script && script.dataset.bundle;
  const cookie = (document.cookie.match(/(?:^|;\s*)lang=([a-z]{2})/) || [])[1];
  const browser = (navigator.languages || [navigator.language || ""])
    .map((l) => String(l || "").slice(0, 2).toLowerCase())
    .find((l) => LANGS.includes(l));
  const lang = LANGS.includes(cookie) ? cookie : browser || "de";
  const exact = new Map();
  const patterns = [];
  const fill = (s, args) => String(s).replace(/\{(\d+)\}/g, (m, i) => (args[i] !== undefined ? args[i] : m));
  const api = {
    lang,
    t: (s, ...args) => fill(lookup(String(s)) ?? s, args),
    ready: Promise.resolve(),
  };
  window.I18N = api;
  document.documentElement.lang = lang;
  if (lang === "de" || !bundle) {
    return;
  }

  const LETTER = /[A-Za-zÄÖÜäöüßÀ-ÿ]/;
  const SKIP = "script,style,textarea,code,pre,[data-no-i18n],[translate='no']";
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  const cache = new Map();

  function addCatalog(data) {
    const list = [];
    for (const [key, value] of Object.entries(data)) {
      if (/\{\d+\}/.test(key)) {
        const parts = key.split(/(\{\d+\})/);
        const order = [];
        const source = parts
          .map((part) => {
            const m = part.match(/^\{(\d+)\}$/);
            if (m) {
              order.push(Number(m[1]));
              return "([\\s\\S]*?)";
            }
            return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          })
          .join("");
        list.push({ re: new RegExp("^" + source + "$"), order, out: value, weight: key.replace(/\{\d+\}/g, "").length });
      } else {
        exact.set(key, value);
      }
    }
    list.sort((a, b) => b.weight - a.weight);
    patterns.push(...list);
  }

  function lookup(core) {
    if (cache.has(core)) {
      return cache.get(core);
    }
    let out = exact.get(core);
    if (out === undefined) {
      out = null;
      for (const p of patterns) {
        const m = p.re.exec(core);
        if (m) {
          const values = [];
          p.order.forEach((index, k) => {
            const v = m[k + 1];
            const inner = v.trim();
            // Eingefangene Teile ("startet", ", 2 ersetzt") ihrerseits uebersetzen
            const t = inner && inner !== core ? lookup(inner) : null;
            values[index] = t !== null && t !== undefined ? v.replace(inner, t) : v;
          });
          out = fill(p.out, values);
          break;
        }
      }
    }
    if (cache.size > 5000) {
      cache.clear();
    }
    cache.set(core, out);
    return out;
  }

  function tr(raw) {
    if (!raw || !LETTER.test(raw)) {
      return null;
    }
    const m = raw.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const core = m[2].replace(/\s+/g, " ");
    const out = lookup(core);
    return out === null || out === undefined ? null : m[1] + out + m[3];
  }

  function translateText(node) {
    const parent = node.parentElement;
    if (!parent || parent.closest(SKIP)) {
      return;
    }
    const out = tr(node.nodeValue);
    if (out !== null && out !== node.nodeValue) {
      node.nodeValue = out;
    }
  }

  function translateAttrs(el) {
    if (el.closest(SKIP)) {
      return;
    }
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (value) {
        const out = tr(value);
        if (out !== null && out !== value) {
          el.setAttribute(name, out);
        }
      }
    }
    if (el.tagName === "INPUT" && /^(button|submit|reset)$/i.test(el.type) && el.value) {
      const out = tr(el.value);
      if (out !== null) {
        el.value = out;
      }
    }
  }

  function translateTree(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      translateText(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) {
      return;
    }
    if (root.nodeType === Node.ELEMENT_NODE) {
      if (root.matches(SKIP)) {
        return;
      }
      translateAttrs(root);
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) {
        translateText(node);
      } else {
        translateAttrs(node);
      }
      node = walker.nextNode();
    }
  }

  // Bis zur ersten Uebersetzung unsichtbar (kein deutsches Aufblitzen); spaetestens nach 1,5 s sichtbar.
  const style = document.createElement("style");
  style.textContent = "html.i18n-pending body{opacity:0!important}";
  document.documentElement.classList.add("i18n-pending");
  document.head ? document.head.append(style) : document.documentElement.append(style);
  const reveal = () => document.documentElement.classList.remove("i18n-pending");
  setTimeout(reveal, 1500);

  // Dialoge und Canvas
  for (const name of ["alert", "confirm", "prompt"]) {
    const original = window[name].bind(window);
    window[name] = (message, ...rest) => original(typeof message === "string" ? tr(message) ?? message : message, ...rest);
  }
  if (script.dataset.canvas !== undefined && window.CanvasRenderingContext2D) {
    for (const name of ["fillText", "strokeText"]) {
      const original = CanvasRenderingContext2D.prototype[name];
      CanvasRenderingContext2D.prototype[name] = function (text, ...rest) {
        return original.call(this, typeof text === "string" && exact.size ? tr(text) ?? text : text, ...rest);
      };
    }
  }

  let observer = null;
  function start() {
    translateTree(document.documentElement);
    observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === "characterData") {
          translateText(m.target);
        } else if (m.type === "attributes") {
          translateAttrs(m.target);
        } else {
          m.addedNodes.forEach(translateTree);
        }
      }
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRS,
    });
    reveal();
  }

  api.ready = fetch(`/i18n/${bundle}/${lang}.json`, { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}))
    .then((data) => {
      addCatalog(data);
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
      } else {
        start();
      }
    });
})();
