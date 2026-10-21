// Drohnen Tools – direkt auf die SD-Karte des Senders schreiben (Theme Studio, Lua Studio)
// Wie im Backup Studio: Sender per USB verbinden, am Sender „USB Storage (SD)“ wählen,
// dann das Laufwerk der Karte wählen. Nur Chrome/Edge am PC (File System Access API).
// Die gewählte Karte merkt sich der Browser (IndexedDB), beim nächsten Mal reicht die Erlaubnis.
//
// WerkstattSD.write(entries, { pickNew, onProgress })
//   entries: [{ name: 'THEMES/Mein/theme.yml', data: Uint8Array }] – Ordner-Einträge (…/) werden übersprungen
//   → { written, replaced, card } oder { cancelled: true }
window.WerkstattSD = (() => {
  'use strict';
  const supported = 'showDirectoryPicker' in window && !window.claude;
  const DB_NAME = 'tx15-werkstatt-sd';

  function db() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => r.result.createObjectStore('handles');
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
  }
  async function getHandle() {
    try {
      const d = await db();
      return await new Promise(res => { const r = d.transaction('handles').objectStore('handles').get('sd'); r.onsuccess = () => res(r.result || null); r.onerror = () => res(null); });
    } catch (e) { return null; }
  }
  async function saveHandle(h) {
    try {
      const d = await db();
      await new Promise(res => { const t = d.transaction('handles', 'readwrite'); h ? t.objectStore('handles').put(h, 'sd') : t.objectStore('handles').delete('sd'); t.oncomplete = res; t.onerror = res; });
    } catch (e) { /* nur Komfort */ }
  }

  // Hauptverzeichnis einer EdgeTX-Karte? (RADIO oder MODELS vorhanden)
  async function checkCard(dir) {
    for await (const [name, handle] of dir.entries()) {
      if (handle.kind === 'directory' && ['RADIO', 'MODELS'].includes(name.toUpperCase())) return;
    }
    throw new Error('Das ist nicht das Hauptverzeichnis einer EdgeTX-SD-Karte – es fehlen die Ordner RADIO und MODELS. Wähle das Laufwerk der Karte selbst, nicht einen Unterordner.');
  }

  async function pickCard() {
    const dir = await window.showDirectoryPicker({ id: 'tx15sd', mode: 'readwrite' });
    await checkCard(dir);
    await saveHandle(dir);
    return dir;
  }

  async function cardHandle(pickNew) {
    if (!pickNew) {
      const h = await getHandle();
      if (h) {
        try {
          let perm = await h.queryPermission({ mode: 'readwrite' });
          if (perm !== 'granted') perm = await h.requestPermission({ mode: 'readwrite' });
          if (perm === 'granted') { await checkCard(h); return h; }
        } catch (e) { /* Karte entfernt oder gewechselt – neu wählen */ }
      }
    }
    return pickCard();
  }

  async function fileExists(dir, path) {
    const parts = path.split('/');
    try {
      let d = dir;
      for (const seg of parts.slice(0, -1)) d = await d.getDirectoryHandle(seg);
      await d.getFileHandle(parts[parts.length - 1]);
      return true;
    } catch (e) { return false; }
  }

  async function writeFile(dir, path, data) {
    const parts = path.split('/');
    let d = dir;
    for (const seg of parts.slice(0, -1)) d = await d.getDirectoryHandle(seg, { create: true });
    const fh = await d.getFileHandle(parts[parts.length - 1], { create: true });
    const w = await fh.createWritable();
    await w.write(data);
    await w.close();
  }

  async function write(entries, o = {}) {
    if (!supported) throw new Error('Direktes Schreiben braucht Chrome oder Edge am PC. Lade das ZIP herunter und entpacke es ins Hauptverzeichnis der SD-Karte.');
    let dir;
    try { dir = await cardHandle(o.pickNew); }
    catch (e) { if (e && e.name === 'AbortError') return { cancelled: true }; throw e; }
    const files = entries.filter(e => !e.name.endsWith('/'));
    const existing = [];
    for (const f of files) if (await fileExists(dir, f.name)) existing.push(f.name);
    if (existing.length && !confirm(`${existing.length === 1 ? 'Diese Datei gibt' : `Diese ${existing.length} Dateien gibt`} es auf der Karte schon – sie werden ersetzt:\n\n${existing.slice(0, 10).join('\n')}${existing.length > 10 ? '\n…' : ''}\n\nFortfahren?`)) return { cancelled: true };
    let n = 0;
    for (const f of files) {
      if (o.onProgress) o.onProgress(n / files.length, f.name);
      await writeFile(dir, f.name, f.data);
      n++;
    }
    if (o.onProgress) o.onProgress(1, '');
    return { written: n, replaced: existing.length, card: dir.name };
  }

  return { supported, write };
})();
