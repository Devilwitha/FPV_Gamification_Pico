// Drohnen Tools – im Bollisoft-Konto auf dem Server speichern (Theme Studio, Lua Studio)
// Nur auf bollisoft.ch: Die Ablage gehört zum Webshop (/api/tx15-projects) und
// funktioniert nur mit Login. Lokal (start-server.ps1) bleibt es beim ZIP-Download.
//
// WerkstattKonto.mount({
//   kind: 'theme' | 'lua', label: 'Theme', storageKey,
//   saveBtn, newBtn, hint,              // Elemente aus der Seite
//   toast(msg, kind),                   // Meldungen der Seite
//   collect: async () => ({ name, detail, state, zip }),   // zip = Blob
//   apply: async state => {},           // gespeicherten Stand ins Studio laden
// })
window.WerkstattKonto = (() => {
  'use strict';
  const API = '/api/tx15-projects';
  const HEADERS = { 'X-Requested-With': 'tx15-werkstatt' };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const isJson = r => (r.headers.get('content-type') || '').includes('json');

  function mount(o) {
    let status = { available: false, loggedIn: false };
    let current = null;   // { id, name } – mit diesem Eintrag im Konto verknüpft
    try { current = JSON.parse(localStorage.getItem(o.storageKey) || 'null'); } catch (e) {}
    const setCurrent = c => {
      current = c;
      try { c ? localStorage.setItem(o.storageKey, JSON.stringify(c)) : localStorage.removeItem(o.storageKey); } catch (e) {}
    };
    const here = () => location.pathname + location.search;
    const authLinks = () => `<a href="/login?next=${encodeURIComponent(here())}">Einloggen</a> oder <a href="/register?next=${encodeURIComponent(here())}">Konto erstellen</a>`;
    const listLink = `<a href="/account#werkstatt">Alle im Konto ansehen</a>`;

    function render() {
      const linked = status.loggedIn && current;
      o.saveBtn.disabled = !status.loggedIn;
      o.saveBtn.textContent = linked ? `„${current.name}“ aktualisieren` : 'Auf dem Server speichern';
      o.newBtn.hidden = !linked;
      o.hint.innerHTML = !status.available
        ? 'Speichern auf dem Server gibt es nur auf <a href="https://www.bollisoft.ch/tx15-werkstatt/">bollisoft.ch</a>.'
        : !status.loggedIn ? `Mit Bollisoft-Konto speicherst du dein ${esc(o.label)} auf dem Server. ${authLinks()}.`
        : linked ? `Verknüpft mit „${esc(current.name)}“ in deinem Konto. „Als neu speichern“ legt eine Kopie an. ${listLink}.`
        : `Speichert in deinem Bollisoft-Konto. ${listLink}.`;
    }

    async function refresh() {
      try {
        const r = await fetch(`${API}?kind=${o.kind}`, { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' });
        if (!r.ok || !isJson(r)) throw new Error();
        const d = await r.json();
        status = { available: true, loggedIn: d.logged_in === true };
        // Im Konto gelöscht? Dann nicht mehr verknüpft.
        if (status.loggedIn && current && !d.projects.some(p => p.id === current.id)) setCurrent(null);
      } catch (e) { status = { available: false, loggedIn: false }; }
      render();
    }

    async function save(asNew) {
      if (!status.loggedIn) return;
      o.saveBtn.disabled = o.newBtn.disabled = true;
      try {
        const p = await o.collect();
        const fd = new FormData();
        fd.append('kind', o.kind); fd.append('name', p.name); fd.append('detail', p.detail || '');
        fd.append('state', new Blob([JSON.stringify(p.state)], { type: 'application/json' }), 'state.json');
        fd.append('file', p.zip, 'export.zip');
        const send = update => fetch(update ? `${API}/${current.id}` : API, { method: update ? 'PUT' : 'POST', headers: HEADERS, body: fd, credentials: 'same-origin' });
        let r = await send(!!current && !asNew);
        if (current && !asNew && r.status === 404) { setCurrent(null); r = await send(false); }
        const d = isJson(r) ? await r.json() : null;
        if (r.status === 401) { status.loggedIn = false; throw new Error((d && d.error) || 'Bitte erneut einloggen.'); }
        if (!r.ok || !d) throw new Error((d && d.error) || (r.status === 404 || r.status === 413 ? 'Zu groß für den Server.' : `Speichern fehlgeschlagen (HTTP ${r.status}).`));
        setCurrent({ id: d.project.id, name: d.project.name });
        o.toast(`„${d.project.name}“ in deinem Konto gespeichert.`, 'ok');
      } catch (e) { o.toast(e.message || 'Speichern fehlgeschlagen.', 'err'); }
      o.saveBtn.disabled = o.newBtn.disabled = false;
      render();
    }

    // Aus dem Konto öffnen: …-studio.html?projekt=<id>
    async function openFromUrl() {
      const id = Number(new URLSearchParams(location.search).get('projekt'));
      if (!id) return;
      if (!status.loggedIn) { if (status.available) o.toast('Zum Öffnen aus deinem Konto bitte einloggen.', 'err'); return; }
      try {
        const r = await fetch(`${API}/${id}`, { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' });
        const d = isJson(r) ? await r.json() : null;
        if (!r.ok || !d) throw new Error((d && d.error) || 'Konnte nicht geladen werden.');
        await o.apply(d.project.state);
        setCurrent({ id: d.project.id, name: d.project.name });
        history.replaceState(null, '', location.pathname);
        o.toast(`„${d.project.name}“ aus deinem Konto geöffnet.`, 'ok');
      } catch (e) { o.toast(e.message, 'err'); }
      render();
    }

    o.saveBtn.onclick = () => save(false);
    o.newBtn.onclick = () => save(true);
    // Nach dem Einloggen in einem anderen Tab beim Zurückkehren neu prüfen.
    window.addEventListener('focus', () => { if (!status.loggedIn) refresh(); });
    render();
    refresh().then(openFromUrl);
    return { refresh };
  }

  return { mount };
})();
