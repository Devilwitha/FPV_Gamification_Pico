// LogDecoder: the decoding worker (worker.ts) behind a promise API – one request at a time.
// Used by the single viewer (log selection, CSV export) and the VS viewer (two flights).
import type { FlightData, LogSummary, WorkerRequest, WorkerResponse } from '../types.ts';

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; expect: WorkerResponse['type'] };

export class LogDecoder {
  private worker: Worker;
  private pending: Pending | null = null;
  /** progress of the running request (0…1) */
  onProgress: ((label: string, value: number) => void) | null = null;

  constructor() {
    this.worker = new Worker(new URL('../worker.ts', import.meta.url), { type: 'module' });
    this.worker.onmessage = (e: MessageEvent<WorkerResponse>) => this.handle(e.data);
  }

  private handle(msg: WorkerResponse) {
    if (msg.type === 'progress') {
      this.onProgress?.(msg.label, msg.value);
      return;
    }
    const p = this.pending;
    if (!p) return;
    this.pending = null;
    if (msg.type === 'error') p.reject(new Error(msg.message));
    else if (msg.type === p.expect) p.resolve(msg);
    else p.reject(new Error(`Unerwartete Antwort: ${msg.type}`));
  }

  private request<T extends WorkerResponse['type']>(msg: WorkerRequest, expect: T, transfer: Transferable[] = []): Promise<Extract<WorkerResponse, { type: T }>> {
    if (this.pending) this.pending.reject(new Error('Abgebrochen.'));
    return new Promise((resolve, reject) => {
      this.pending = { resolve: resolve as (v: unknown) => void, reject, expect };
      this.worker.postMessage(msg, transfer);
    });
  }

  /** Read a log file; resolves with the logs found in it (a flash dump can hold several). */
  async open(file: Blob, name: string): Promise<{ logs: LogSummary[]; source: 'bbl' | 'csv' }> {
    const buffer = await file.arrayBuffer();
    const res = await this.request({ type: 'load', name, buffer }, 'logs', [buffer]);
    return { logs: res.logs, source: res.source };
  }

  /** Decode one log of the opened file. */
  async decode(index: number): Promise<FlightData> {
    return (await this.request({ type: 'process', index }, 'flight')).flight;
  }

  /** Raw data of the current log as CSV. */
  async csv(): Promise<{ blob: Blob; name: string }> {
    const res = await this.request({ type: 'csv' }, 'csv');
    return { blob: res.blob, name: res.name };
  }

  terminate() {
    this.pending?.reject(new Error('Abgebrochen.'));
    this.pending = null;
    this.worker.terminate();
  }
}

/** Index of the largest log – usually the actual flight. */
export const largestLog = (logs: LogSummary[]) => logs.reduce((a, b) => (b.bytes > a.bytes ? b : a), logs[0]).index;

/** Decode a file in one go (largest log unless `pick` is given) with a temporary worker. */
export async function decodeFlight(file: Blob, name: string, pick?: number, onProgress?: (label: string, value: number) => void) {
  const decoder = new LogDecoder();
  decoder.onProgress = onProgress ?? null;
  try {
    const { logs } = await decoder.open(file, name);
    if (!logs.length) throw new Error('Keine Logs in der Datei gefunden.');
    const index = pick !== undefined && logs.some((l) => l.index === pick) ? pick : largestLog(logs);
    return { flight: await decoder.decode(index), index, count: logs.length };
  } finally {
    decoder.terminate();
  }
}
