/// <reference lib="webworker" />
// Decoding runs off the main thread: .bbl/.bfl binary or CSV → FlightData, plus raw CSV export.
import { decodeLog, findLogs, type LogSpan } from './decoder/blackbox.ts';
import { parseCsvLog } from './decoder/csv.ts';
import { NEEDED, columnsToSeries, gyroToDegFromHeaders, type Columns } from './pipeline/columns.ts';
import { buildFlightData } from './pipeline/process.ts';
import type { FlightData, LogEvent, WorkerRequest, WorkerResponse } from './types.ts';

declare const self: DedicatedWorkerGlobalScope;

let bytes: Uint8Array | null = null;
let fileName = '';
let spans: LogSpan[] = [];
let source: 'bbl' | 'csv' = 'bbl';
let currentIndex = 0;

const post = (msg: WorkerResponse, transfer: Transferable[] = []) => self.postMessage(msg, transfer);
const progress = (value: number, label: string) => post({ type: 'progress', value, label });

class Grow {
  arr = new Float64Array(1 << 14);
  n = 0;
  push(v: number) {
    if (this.n === this.arr.length) {
      const next = new Float64Array(this.arr.length * 2);
      next.set(this.arr);
      this.arr = next;
    }
    this.arr[this.n++] = v;
  }
  get data() {
    return this.arr.subarray(0, this.n);
  }
}

function looksLikeCsv(data: Uint8Array): boolean {
  const head = new TextDecoder().decode(data.subarray(0, Math.min(data.length, 4096)));
  return !head.includes('H Product:') && /time/i.test(head) && head.includes(',');
}

function processBbl(index: number): FlightData {
  const span = spans[index];
  const collectors = new Map<string, { idx: number; g: Grow }>();
  let timeIdx = 1;
  const result = decodeLog(bytes!, span.start, span.end, {
    onHeaders: (_h, def) => {
      def.names.forEach((name, idx) => {
        if (NEEDED.has(name)) collectors.set(name, { idx, g: new Grow() });
      });
      timeIdx = def.names.indexOf('time');
    },
    onFrame: (frame) => {
      for (const c of collectors.values()) c.g.push(frame[c.idx]);
    },
    onProgress: (f) => progress(f * 0.85, 'Dekodiere Frames…'),
  });
  if (timeIdx < 0) throw new Error('Log enthält kein "time"-Feld');
  const cols: Columns = new Map();
  for (const [name, c] of collectors) cols.set(name, c.g.data);
  const time = cols.get('time');
  if (!time || time.length < 10) throw new Error(`Log ${index + 1} enthält zu wenige gültige Frames (${time?.length ?? 0}).`);

  const h = result.headers;
  const acc1g = parseFloat(h['acc_1G'] ?? '');
  progress(0.9, 'Berechne Lage (Attitude)…');
  const series = columnsToSeries(cols, h, {
    timeToSec: 1e-6,
    gyroToDeg: gyroToDegFromHeaders(h),
    accScale: Number.isFinite(acc1g) && acc1g > 0 ? acc1g : null,
  });
  const t0 = series.t[0];
  const events: LogEvent[] = result.events.map((e) => ({ type: e.type, time: e.time / 1e6 - t0, value: e.value }));
  return buildFlightData(
    series,
    {
      source: 'bbl',
      fileName,
      firmware: h['Firmware revision'] ?? h['Firmware type'] ?? 'unbekannt',
      craft: h['Craft name'] ?? '',
      fields: result.mainDef.names,
      headers: h,
      logIndex: index,
      logCount: spans.length,
      corruptFrames: result.stats.corrupt,
    },
    events,
    t0,
  );
}

function processCsv(): FlightData {
  progress(0.1, 'Lese CSV…');
  const text = new TextDecoder().decode(bytes!);
  const csv = parseCsvLog(text);
  progress(0.7, 'Berechne Lage (Attitude)…');
  const series = columnsToSeries(csv.columns, csv.headers, csv.units);
  return buildFlightData(
    series,
    {
      source: 'csv',
      fileName,
      firmware: csv.headers['Firmware revision'] ?? csv.headers['Firmware type'] ?? 'CSV',
      craft: csv.headers['Craft name'] ?? '',
      fields: csv.names,
      headers: csv.headers,
      logIndex: 0,
      logCount: 1,
      corruptFrames: 0,
    },
    [],
  );
}

function transferables(f: FlightData): Transferable[] {
  const list: ArrayBufferLike[] = [f.t.buffer, f.throttle.buffer, f.quat.buffer, ...f.gyro.map((a) => a.buffer), ...f.sticks.map((a) => a.buffer)];
  if (f.acc) list.push(...f.acc.map((a) => a.buffer));
  if (f.motor) list.push(f.motor.buffer);
  return [...new Set(list)] as Transferable[];
}

function exportCsv(): Blob {
  if (source === 'csv') return new Blob([bytes as Uint8Array<ArrayBuffer>], { type: 'text/csv' });
  const span = spans[currentIndex];
  const chunks: string[] = [];
  let buf = '';
  decodeLog(bytes!, span.start, span.end, {
    onHeaders: (_h, def) => {
      buf = def.names.join(',') + '\n';
    },
    onFrame: (frame) => {
      buf += frame.join(',') + '\n';
      if (buf.length > 1 << 20) {
        chunks.push(buf);
        buf = '';
      }
    },
    onProgress: (f) => progress(f, 'Exportiere CSV…'),
  });
  chunks.push(buf);
  return new Blob(chunks, { type: 'text/csv' });
}

self.onmessage = (e: MessageEvent<WorkerRequest>) => {
  const msg = e.data;
  try {
    switch (msg.type) {
      case 'load': {
        bytes = new Uint8Array(msg.buffer);
        fileName = msg.name;
        if (looksLikeCsv(bytes)) {
          source = 'csv';
          spans = [{ start: 0, end: bytes.length }];
        } else {
          source = 'bbl';
          spans = findLogs(bytes);
          if (!spans.length) throw new Error('Keine Blackbox-Logs in der Datei gefunden (fehlender "H Product:"-Header).');
        }
        post({ type: 'logs', source, logs: spans.map((s, index) => ({ index, bytes: s.end - s.start })) });
        break;
      }
      case 'process': {
        if (!bytes) throw new Error('Keine Datei geladen');
        currentIndex = msg.index;
        const flight = source === 'csv' ? processCsv() : processBbl(msg.index);
        post({ type: 'flight', flight }, transferables(flight));
        break;
      }
      case 'csv': {
        if (!bytes) throw new Error('Keine Datei geladen');
        const base = fileName.replace(/\.[^.]+$/, '');
        post({ type: 'csv', blob: exportCsv(), name: `${base}${spans.length > 1 ? `.${currentIndex + 1}` : ''}.csv` });
        break;
      }
    }
  } catch (err) {
    post({ type: 'error', message: err instanceof Error ? err.message : String(err) });
  }
};
