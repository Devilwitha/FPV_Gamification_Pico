// CSV import: output of blackbox_decode or the CSV export of Blackbox Explorer.
import { NEEDED, type ColumnUnits, type Columns } from '../pipeline/columns.ts';

export interface CsvLog {
  names: string[];
  columns: Columns;
  headers: Record<string, string>;
  units: ColumnUnits;
  rows: number;
}

const clean = (s: string) => s.trim().replace(/^"|"$/g, '').trim();

/** "gyroADC[0] (deg/s)" → ["gyroADC[0]", "deg/s"] */
function splitUnit(name: string): [string, string] {
  const m = name.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
  return m ? [m[1].trim(), m[2].trim().toLowerCase()] : [name.trim(), ''];
}

export function parseCsvLog(text: string): CsvLog {
  const lines = text.split(/\r?\n/);
  const headers: Record<string, string> = {};
  let headerRow = -1;
  for (let i = 0; i < Math.min(lines.length, 2000); i++) {
    const cells = lines[i].split(',').map(clean);
    const lower = cells.map((c) => c.toLowerCase());
    if (lower.some((c) => c === 'time' || c.startsWith('time ')) && lower.some((c) => c.startsWith('gyro'))) {
      headerRow = i;
      break;
    }
    if (cells.length >= 2 && cells[0]) headers[cells[0]] = cells.slice(1).join(',');
  }
  if (headerRow < 0) throw new Error('CSV: keine Kopfzeile mit "time" und "gyro…" Spalten gefunden');

  const rawNames = lines[headerRow].split(',').map(clean);
  const names: string[] = [];
  const unitOf = new Map<string, string>();
  for (const raw of rawNames) {
    const [n, u] = splitUnit(raw);
    names.push(n);
    unitOf.set(n, u);
  }
  const keep = names.map((n) => NEEDED.has(n));

  const cap = lines.length - headerRow;
  const cols: Float64Array[] = names.map((_, i) => (keep[i] ? new Float64Array(cap) : new Float64Array(0)));
  let rows = 0;
  for (let i = headerRow + 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;
    const cells = line.split(',');
    if (cells.length < names.length - 1) continue;
    const t = parseFloat(cells[names.indexOf('time')]);
    if (!Number.isFinite(t)) continue;
    for (let c = 0; c < names.length; c++) if (keep[c]) cols[c][rows] = parseFloat(cells[c]) || 0;
    rows++;
  }
  const columns: Columns = new Map();
  names.forEach((n, i) => {
    if (keep[i]) columns.set(n, cols[i].subarray(0, rows));
  });

  // --- units ---
  const time = columns.get('time');
  let timeToSec = 1e-6;
  const tUnit = unitOf.get('time') ?? '';
  if (tUnit === 's') timeToSec = 1;
  else if (tUnit === 'ms') timeToSec = 1e-3;
  else if (tUnit !== 'us' && time && rows > 2) {
    const diffs: number[] = [];
    for (let i = 1; i < Math.min(rows, 2000); i++) diffs.push(time[i] - time[i - 1]);
    diffs.sort((a, b) => a - b);
    const dt = diffs[diffs.length >> 1];
    timeToSec = dt >= 20 ? 1e-6 : dt >= 0.02 ? 1e-3 : 1;
  }
  const gUnit = unitOf.get('gyroADC[0]') ?? '';
  const gyroToDeg = gUnit.includes('rad') ? 180 / Math.PI : 1;
  const aUnit = unitOf.get('accSmooth[0]') ?? '';
  const acc1g = parseFloat(headers['acc_1G'] ?? '');
  const accScale = aUnit === 'g' ? 1 : aUnit.startsWith('m/s') ? 9.80665 : Number.isFinite(acc1g) && acc1g > 0 ? acc1g : null;

  return { names, columns, headers, units: { timeToSec, gyroToDeg, accScale }, rows };
}
