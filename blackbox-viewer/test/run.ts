// Run with: npm test   (Node ≥ 22.6 with built-in TypeScript type stripping)
import { simulateFlight } from '../src/demo/simulate.ts';
import { DEFAULT_SETTINGS, detectTricks, summarize } from '../src/analysis/tricks.ts';
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

// --- .bbl decoder round trip ---
failed += testBblRoundTrip(check);

if (failed) {
  console.error(`\n${failed} Test(s) fehlgeschlagen`);
  process.exit(1);
}
console.log('\nAlle Tests bestanden');
