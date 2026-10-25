// Run with: npm test   (Node ≥ 22.6 with built-in TypeScript type stripping)
import { simulateFlight } from '../src/demo/simulate.ts';
import { DEFAULT_SETTINGS, detectTricks, registerCustomTricks, scoreTricks, summarize } from '../src/analysis/tricks.ts';
import type { Trick } from '../src/types.ts';
import { estimateCells } from '../src/pipeline/columns.ts';
import { fingerprints } from '../src/ui/leaderboard.ts';
import { testBblRoundTrip } from './bbl.test.ts';

let failed = 0;
function check(name: string, cond: boolean, info = '') {
  console.log(`${cond ? '✔' : '✘'} ${name}${info ? ` – ${info}` : ''}`);
  if (!cond) failed++;
}

// --- trick detection on the synthetic demo flight ---
const flight = simulateFlight();
const { tricks, rejected } = detectTricks(flight, DEFAULT_SETTINGS);
for (const t of tricks) console.log(`   ${t.start.toFixed(2).padStart(6)}s  ${t.name.padEnd(14)} ${String(t.points).padStart(5)} P  combo×${t.comboMult}  [${t.angles.map((a) => Math.round(a)).join(', ')}]`);
console.log(`   rejected: ${rejected.map((r) => r.start.toFixed(1)).join(', ')}`);
const expected = [
  'Roll', 'Double Roll', 'Flip', 'Power Loop', 'Split-S', 'Yaw Spin', 'Roll', 'Flip', 'Immelmann', 'Triple Roll', 'Corkscrew',
  'Orbit', 'Power Loop', 'Roll', 'Inverted Hang', 'Trippy Spin', 'Dive', 'Matty Flip', 'Helix',
];
check('Demo: erwartete Trick-Sequenz', JSON.stringify(tricks.map((t) => t.name)) === JSON.stringify(expected), tricks.map((t) => t.name).join(', '));
const s = summarize(tricks);
check('Demo: Score > 0 und Combos', s.total > 0 && s.maxCombo >= 2, `total=${s.total}, maxCombo=${s.maxCombo}`);

// --- repeats: same trick in a row scores less, max. 2 identical tricks per combo ---
const mk = (type: string, start: number): Trick => ({
  id: 0, key: '', type, name: type, detail: '', count: 1, start, end: start + 0.5, angles: [360, 0, 0], duration: 0.5,
  meanThrottle: 0.5, peakRate: 800, purity: 1, basePoints: 0, comboLevel: 0, comboMult: 1, repeat: 0, repeatMult: 1,
  points: 0, source: 'rules', features: [], axis: 0,
});
const seq = [mk('roll', 0), mk('roll', 1), mk('roll', 2), mk('roll', 3), mk('flip', 4), mk('flip', 5), mk('roll', 6)];
scoreTricks(seq, DEFAULT_SETTINGS);
check('Wiederholung: Punkte-Faktor 1 / ½ / ¼ / ⅛', JSON.stringify(seq.slice(0, 4).map((t) => t.repeatMult)) === JSON.stringify([1, 0.5, 0.25, 0.125]),
  seq.map((t) => t.repeatMult).join(', '));
check('Wiederholung: Basispunkte sinken', seq[0].basePoints === 100 && seq[1].basePoints === 50 && seq[2].basePoints === 25, seq.map((t) => t.basePoints).join(', '));
check('Wiederholung: höchstens 2 gleiche je Combo', JSON.stringify(seq.map((t) => t.comboLevel)) === JSON.stringify([0, 1, 0, 0, 1, 2, 3]),
  seq.map((t) => t.comboLevel).join(', '));
check('Wiederholung: anderer Trick setzt zurück', seq[4].repeat === 0 && seq[5].repeat === 1 && seq[6].repeat === 0 && seq[6].repeatMult === 1);
const apart = [mk('roll', 0), mk('roll', 20)];
scoreTricks(apart, DEFAULT_SETTINGS);
check('Wiederholung: lange Pause zählt nicht', apart[1].repeat === 0 && apart[1].basePoints === 100);
const demoRepeats = tricks.filter((t) => t.repeat > 0).map((t) => `${t.name}@${t.start.toFixed(1)}×${t.repeatMult}`);
check('Wiederholung: Demo-Flug (Roll → Double Roll)', demoRepeats.length >= 1, demoRepeats.join(', '));

// --- battery class from the start voltage ---
const cellCases: [number | null, number | null][] = [
  [4.3, 1], [8.4, 2], [7.4, 2], [12.6, 3], [13.2, 3], [11.1, 3], [16.8, 4], [14.0, 4], [15.2, 4],
  [25.2, 6], [21.0, 6], [22.2, 6], [33.6, 8], [29.6, 8], [null, null], [1.2, null],
];
const cellFails = cellCases.filter(([v, c]) => estimateCells(v) !== c);
check('Akku: Zellenzahl aus Spannung', !cellFails.length, cellFails.map(([v, c]) => `${v} V → ${estimateCells(v)} statt ${c}`).join(', '));
check('Akku: Demo-Flug als 4S erkannt', flight.info.cells === 4 && flight.info.vbat === 16.6, `${flight.info.vbat} V → ${flight.info.cells}`);

// --- community model: approved examples find tricks the rules miss ---
const rolls = tricks.filter((t) => t.type === 'roll' && t.count === 1);
const strict = { ...DEFAULT_SETTINGS, startRate: 5000 }; // rules find no rotation at all
const blind = detectTricks(flight, strict);
check('Community: ohne Beispiele keine Rolls (strenge Regeln)', !blind.tricks.some((t) => t.type === 'roll'));
const started = performance.now();
const learnedRun = detectTricks(flight, strict, { learned: rolls.slice(0, 1).map((t) => ({ label: 'roll', f: t.features })) });
const ms = performance.now() - started;
const found = learnedRun.tricks.filter((t) => t.source === 'community' && t.type === 'roll');
const hitsAll = rolls.every((r) => found.some((f) => Math.abs(f.start - r.start) < 0.4));
check('Community: Rolls aus einem Beispiel wiedergefunden', hitsAll, `${found.length} gefunden: ${found.map((f) => f.start.toFixed(1)).join(', ')} – erwartet ${rolls.map((r) => r.start.toFixed(1)).join(', ')}`);
check('Community: keine Treffer am Boden (vor dem Start)', !learnedRun.tricks.some((t) => t.start < 2));
registerCustomTricks([{ key: 'c99', name: 'Testtrick', points: 300 }]);
const corkscrew = tricks.find((t) => t.type === 'corkscrew')!;
const custom = detectTricks(flight, strict, { learned: [{ label: 'c99', f: corkscrew.features }] });
const hit = custom.tricks.find((t) => t.type === 'c99');
check('Community: eigener Trick erkannt und bepunktet', !!hit && hit.name === 'Testtrick' && hit.points > 0 && Math.abs(hit.start - corkscrew.start) < 0.4,
  hit ? `${hit.name} @ ${hit.start.toFixed(2)} (${hit.points} P)` : 'nicht gefunden');
const normal = detectTricks(flight, DEFAULT_SETTINGS, { learned: [{ label: 'c99', f: corkscrew.features }] });
check('Community: belegte Bereiche bleiben bei den Regeln', JSON.stringify(normal.tricks.filter((t) => t.source !== 'community').map((t) => t.name)) === JSON.stringify(expected));
const unknown = detectTricks(flight, strict, { learned: [{ label: 'c12345', f: corkscrew.features }] });
check('Community: unbekannte Tricks werden ignoriert', !unknown.tricks.some((t) => t.type === 'c12345'));
registerCustomTricks([]);
const many = tricks.map((t) => ({ label: t.type, f: t.features }));
const t0 = performance.now();
detectTricks(flight, strict, { learned: [...many, ...many, ...many] });
const msMany = performance.now() - t0;
check('Community: Suche schnell genug', ms < 1500 && msMany < 3000, `${ms.toFixed(0)} ms (1 Beispiel), ${msMany.toFixed(0)} ms (${many.length * 3} Beispiele)`);

// --- duplicate check: fingerprints (server computes the log fingerprint the same way) ---
const fp1 = await fingerprints(flight, new Blob(['H Product:test log']), 2, new Blob(['video-bytes']));
const fp2 = await fingerprints(simulateFlight(), null, 0, null);
check('Fingerabdruck: Log wie auf dem Server', fp1.logHash === '35a05deb7be5abceaee9109be3a1c310c5437a013a088f4322f9f09de4ceba79', String(fp1.logHash));
check('Fingerabdruck: Flugdaten stabil', fp1.flightHash === fp2.flightHash && /^[0-9a-f]{64}$/.test(fp1.flightHash ?? ''));
const other = simulateFlight();
other.gyro[0][5000] += 400;
const fp3 = await fingerprints(other, null, 0, null);
check('Fingerabdruck: anderer Flug anders', fp3.flightHash !== fp1.flightHash);
check('Fingerabdruck: Video', /^[0-9a-f]{64}$/.test(fp1.videoHash ?? '') && fp2.videoHash === null && fp2.logHash === null);

// --- .bbl decoder round trip ---
failed += testBblRoundTrip(check);

if (failed) {
  console.error(`\n${failed} Test(s) fehlgeschlagen`);
  process.exit(1);
}
console.log('\nAlle Tests bestanden');
