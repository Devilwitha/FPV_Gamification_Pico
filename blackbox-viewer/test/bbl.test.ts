// Round-trip test: encode a synthetic log with a port of Betaflight's blackbox writer
// (blackbox.c / blackbox_io.c), then decode it with our parser and compare.
import { decodeLog, findLogs } from '../src/decoder/blackbox.ts';
import { NEEDED, columnsToSeries, gyroToDegFromHeaders } from '../src/pipeline/columns.ts';
import { buildFlightData } from '../src/pipeline/process.ts';

class Writer {
  bytes: number[] = [];
  byte(v: number) {
    this.bytes.push(v & 0xff);
  }
  str(s: string) {
    for (const c of s) this.byte(c.charCodeAt(0));
  }
  uvb(v: number) {
    v >>>= 0;
    while (v > 127) {
      this.byte(v | 0x80);
      v >>>= 7;
    }
    this.byte(v);
  }
  svb(v: number) {
    this.uvb((v << 1) ^ (v >> 31));
  }
  tag8_4s16(values: number[]) {
    let selector = 0;
    for (let x = 3; x >= 0; x--) {
      selector <<= 2;
      const v = values[x];
      if (v === 0) selector |= 0;
      else if (v < 8 && v >= -8) selector |= 1;
      else if (v < 128 && v >= -128) selector |= 2;
      else selector |= 3;
    }
    this.byte(selector);
    let nibble = 0;
    let buffer = 0;
    for (let x = 0; x < 4; x++, selector >>= 2) {
      const v = values[x];
      switch (selector & 3) {
        case 1:
          if (nibble === 0) {
            buffer = (v << 4) & 0xff;
            nibble = 1;
          } else {
            this.byte(buffer | (v & 0x0f));
            nibble = 0;
          }
          break;
        case 2:
          if (nibble === 0) this.byte(v);
          else {
            this.byte(buffer | ((v >> 4) & 0x0f));
            buffer = (v << 4) & 0xff;
          }
          break;
        case 3:
          if (nibble === 0) {
            this.byte(v >> 8);
            this.byte(v);
          } else {
            this.byte(buffer | ((v >> 12) & 0x0f));
            this.byte(v >> 4);
            buffer = (v << 4) & 0xff;
          }
          break;
      }
    }
    if (nibble === 1) this.byte(buffer);
  }
  tag2_3s32(values: number[]) {
    const fits = (bits: number) => values.every((v) => v >= -(1 << (bits - 1)) && v < 1 << (bits - 1));
    if (fits(2)) this.byte(((values[0] & 3) << 4) | ((values[1] & 3) << 2) | (values[2] & 3));
    else if (fits(4)) {
      this.byte((1 << 6) | (values[0] & 0x0f));
      this.byte(((values[1] & 0x0f) << 4) | (values[2] & 0x0f));
    } else if (fits(6)) {
      this.byte((2 << 6) | (values[0] & 0x3f));
      this.byte(values[1] & 0x3f);
      this.byte(values[2] & 0x3f);
    } else {
      let sel = 0;
      const sizes = values.map((v) => (v >= -128 && v < 128 ? 0 : v >= -32768 && v < 32768 ? 1 : v >= -8388608 && v < 8388608 ? 2 : 3));
      for (let x = 2; x >= 0; x--) sel = (sel << 2) | sizes[x];
      this.byte(0xc0 | sel);
      values.forEach((v, x) => {
        for (let b = 0; b <= sizes[x]; b++) this.byte(v >> (8 * b));
      });
    }
  }
  tag2_3svariable(values: number[]) {
    const fit = (v: number, bits: number) => v >= -(1 << (bits - 1)) && v < 1 << (bits - 1);
    if (values.every((v) => fit(v, 2))) this.byte(((values[0] & 3) << 4) | ((values[1] & 3) << 2) | (values[2] & 3));
    else if (fit(values[0], 5) && fit(values[1], 5) && fit(values[2], 4)) {
      this.byte((1 << 6) | ((values[0] & 0x1f) << 1) | ((values[1] & 0x1f) >> 4));
      this.byte(((values[1] & 0x0f) << 4) | (values[2] & 0x0f));
    } else if (fit(values[0], 8) && fit(values[1], 7) && fit(values[2], 7)) {
      this.byte((2 << 6) | ((values[0] & 0xff) >> 2));
      this.byte(((values[0] & 0x03) << 6) | ((values[1] & 0x7f) >> 1));
      this.byte(((values[1] & 0x01) << 7) | (values[2] & 0x7f));
    } else this.tag2_3s32Wide(values);
  }
  private tag2_3s32Wide(values: number[]) {
    let sel = 0;
    const sizes = values.map((v) => (v >= -128 && v < 128 ? 0 : v >= -32768 && v < 32768 ? 1 : v >= -8388608 && v < 8388608 ? 2 : 3));
    for (let x = 2; x >= 0; x--) sel = (sel << 2) | sizes[x];
    this.byte(0xc0 | sel);
    values.forEach((v, x) => {
      for (let b = 0; b <= sizes[x]; b++) this.byte(v >> (8 * b));
    });
  }
  tag8_8svb(values: number[]) {
    if (values.length === 1) return this.svb(values[0]);
    let header = 0;
    for (let i = values.length - 1; i >= 0; i--) header = (header << 1) | (values[i] !== 0 ? 1 : 0);
    this.byte(header);
    for (const v of values) if (v !== 0) this.svb(v);
  }
}

const NAMES = [
  'loopIteration', 'time',
  'axisP[0]', 'axisP[1]', 'axisP[2]',
  'gyroADC[0]', 'gyroADC[1]', 'gyroADC[2]',
  'accSmooth[0]', 'accSmooth[1]', 'accSmooth[2]',
  'rcCommand[0]', 'rcCommand[1]', 'rcCommand[2]', 'rcCommand[3]',
  'motor[0]', 'motor[1]', 'motor[2]', 'motor[3]',
  'debug[0]', 'debug[1]', 'debug[2]',
];
const MINTHROTTLE = 1070;
const MOTOR_LOW = 48;

function frameValues(iter: number, rnd: () => number): number[] {
  const t = 1_000_000 + iter * 250 + Math.floor(rnd() * 3);
  const g = (k: number) => Math.round(800 * Math.sin(iter / (40 + k * 7)) + (rnd() - 0.5) * 40);
  const m = (k: number) => 300 + Math.round(900 * Math.abs(Math.sin(iter / (90 + k)))) + Math.floor(rnd() * 20);
  return [
    iter, t,
    Math.round((rnd() - 0.5) * 60), Math.round((rnd() - 0.5) * 6), Math.round((rnd() - 0.5) * 100000),
    g(0), g(1), g(2),
    Math.round((rnd() - 0.5) * 40), Math.round((rnd() - 0.5) * 200), 2048 + Math.round((rnd() - 0.5) * 30),
    Math.round(400 * Math.sin(iter / 50)), Math.round((rnd() - 0.5) * 14), Math.round((rnd() - 0.5) * 3000), 1100 + Math.round(600 * Math.abs(Math.sin(iter / 120))),
    m(0), m(1), m(2), m(3),
    iter % 3 === 0 ? 0 : Math.round((rnd() - 0.5) * 500), 0, Math.round((rnd() - 0.5) * 5),
  ];
}

function encodeLog(frames: number[][], garbageAt: number): Uint8Array {
  const w = new Writer();
  const H = (k: string, v: string) => w.str(`H ${k}:${v}\n`);
  H('Product', 'Blackbox flight data recorder by Nicholas Sherlock');
  H('Data version', '2');
  H('I interval', '32');
  H('P interval', '1/2');
  H('Field I name', NAMES.join(','));
  H('Field I signed', '0,0,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,1,1,1');
  H('Field I predictor', '0,0,0,0,0,0,0,0,0,0,0,0,0,0,4,11,5,5,5,0,0,0');
  H('Field I encoding', '1,1,0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,0');
  H('Field P predictor', '6,2,1,1,1,3,3,3,1,1,1,1,1,1,1,3,3,3,3,1,1,1');
  H('Field P encoding', '9,0,7,7,7,0,0,0,10,10,10,8,8,8,8,0,0,0,0,6,6,6');
  H('Field S name', 'flightModeFlags,stateFlags');
  H('Field S signed', '0,0');
  H('Field S predictor', '0,0');
  H('Field S encoding', '1,1');
  H('Firmware revision', 'Betaflight 4.5.0 (test)');
  H('minthrottle', String(MINTHROTTLE));
  H('motorOutput', `${MOTOR_LOW},2047`);
  H('acc_1G', '2048');
  w.byte(0x45); w.byte(0); w.uvb(frames[0][1] - 5000); // sync beep event

  let p1: number[] | null = null;
  let p2: number[] | null = null;
  frames.forEach((f, idx) => {
    if (idx === garbageAt) for (const b of [0x50, 0xff, 0xff, 0xff, 0xff, 0x13]) w.byte(b);
    if (f[0] % 32 === 0) {
      w.byte(0x49);
      w.uvb(f[0]); w.uvb(f[1]);
      for (let i = 2; i <= 13; i++) w.svb(f[i]);
      w.uvb(f[14] - MINTHROTTLE);
      w.uvb(f[15] - MOTOR_LOW);
      for (let i = 16; i <= 18; i++) w.svb(f[i] - f[15]);
      for (let i = 19; i <= 21; i++) w.svb(f[i]);
      p1 = p2 = f;
    } else {
      const a = p1!, b = p2!;
      w.byte(0x50);
      w.svb(f[1] - (2 * a[1] - b[1]));
      w.tag2_3s32([f[2] - a[2], f[3] - a[3], f[4] - a[4]]);
      for (let i = 5; i <= 7; i++) w.svb(f[i] - Math.trunc((a[i] + b[i]) / 2));
      w.tag2_3svariable([f[8] - a[8], f[9] - a[9], f[10] - a[10]]);
      w.tag8_4s16([f[11] - a[11], f[12] - a[12], f[13] - a[13], f[14] - a[14]]);
      for (let i = 15; i <= 18; i++) w.svb(f[i] - Math.floor((a[i] + b[i]) / 2));
      w.tag8_8svb([f[19] - a[19], f[20] - a[20], f[21] - a[21]]);
      p2 = a;
      p1 = f;
    }
    if (idx % 50 === 25) {
      w.byte(0x53); w.uvb(idx); w.uvb(3);
    }
  });
  w.byte(0x45); w.byte(15); w.uvb(4); // disarm
  w.byte(0x45); w.byte(255); w.str('End of log\0');
  return new Uint8Array(w.bytes);
}

export function testBblRoundTrip(check: (name: string, ok: boolean, info?: string) => void): number {
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296);
  const frames: number[][] = [];
  for (let iter = 0; iter < 6000; iter += 2) frames.push(frameValues(iter, rnd));
  const garbageAt = 1500;
  const log = encodeLog(frames, garbageAt);
  // two logs back-to-back, as on a flash chip
  const file = new Uint8Array(log.length * 2);
  file.set(log, 0);
  file.set(log, log.length);

  const spans = findLogs(file);
  let fails = 0;
  const ok = (name: string, cond: boolean, info = '') => {
    check(name, cond, info);
    if (!cond) fails++;
  };
  ok('BBL: zwei Logs in Flash-Dump gefunden', spans.length === 2, `${spans.length}`);

  const decoded: number[][] = [];
  const res = decodeLog(file, spans[0].start, spans[0].end, { onFrame: (f) => decoded.push(Array.from(f)) });
  // frames around the garbage are lost until the next I-frame – compare by iteration
  const byIter = new Map(decoded.map((f) => [f[0], f]));
  let mismatches = 0;
  let firstBad = '';
  for (const f of frames) {
    const d = byIter.get(f[0]);
    if (!d) continue;
    for (let i = 0; i < f.length; i++) {
      if (d[i] !== f[i]) {
        mismatches++;
        if (!firstBad) firstBad = `iter ${f[0]} field ${NAMES[i]}: ${d[i]} ≠ ${f[i]}`;
      }
    }
  }
  const lost = frames.length - byIter.size;
  ok('BBL: alle dekodierten Frames identisch', mismatches === 0, firstBad || `${decoded.length} Frames`);
  ok('BBL: Resync nach korrupten Bytes (max. 1 I-Intervall verloren)', lost > 0 && lost <= 17, `${lost} Frames verworfen, ${res.stats.corrupt} korrupt`);
  ok('BBL: Events (Sync-Beep, Disarm, Log-Ende)', res.events.map((e) => e.type).join(',') === 'sync_beep,disarm,log_end', res.events.map((e) => e.type).join(','));
  // full pipeline as in the worker: columns → series → FlightData
  const cols = new Map<string, Float64Array>();
  res.mainDef.names.forEach((n, i) => {
    if (NEEDED.has(n)) cols.set(n, Float64Array.from(decoded, (f) => f[i]));
  });
  const series = columnsToSeries(cols, res.headers, { timeToSec: 1e-6, gyroToDeg: gyroToDegFromHeaders(res.headers), accScale: 2048 });
  const flight = buildFlightData(series, { source: 'bbl', fileName: 't.bbl', firmware: '', craft: '', fields: [], headers: res.headers, logIndex: 0, logCount: 2, corruptFrames: 0 }, []);
  ok('Pipeline: FlightData (Rate, Dauer, Gas, Motoren)', Math.abs(flight.rate - 500) < 30 && flight.info.duration > 1.4 && flight.motor !== null && flight.acc !== null && flight.throttle.every((v) => v >= 0 && v <= 1), `${flight.rate.toFixed(0)} Hz, ${flight.info.duration.toFixed(2)} s`);
  return fails;
}
