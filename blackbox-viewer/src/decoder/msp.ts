// Phase 1: download the onboard flash (dataflash) of a Betaflight FC over USB using Web Serial + MSP v2.
/* Minimal Web Serial typings (not yet part of lib.dom). */
interface SerialPortLike {
  open(o: { baudRate: number }): Promise<void>;
  close(): Promise<void>;
  readable: ReadableStream<Uint8Array> | null;
  writable: WritableStream<Uint8Array> | null;
}
interface SerialLike {
  requestPort(o?: object): Promise<SerialPortLike>;
}

const MSP_FC_VARIANT = 2;
const MSP_FC_VERSION = 3;
const MSP_DATAFLASH_SUMMARY = 70;
const MSP_DATAFLASH_READ = 71;
const MSP_SDCARD_SUMMARY = 79;

export const serialSupported = () => 'serial' in navigator;

function crc8(crc: number, b: number) {
  crc ^= b;
  for (let i = 0; i < 8; i++) crc = crc & 0x80 ? ((crc << 1) ^ 0xd5) & 0xff : (crc << 1) & 0xff;
  return crc;
}

export interface FlashSummary {
  ready: boolean;
  supported: boolean;
  totalSize: number;
  usedSize: number;
}

export class MspClient {
  private port: SerialPortLike | null = null;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private writer: WritableStreamDefaultWriter<Uint8Array> | null = null;
  private buf = new Uint8Array(0);
  private pending: { cmd: number; resolve: (d: Uint8Array) => void; reject: (e: Error) => void } | null = null;
  private closed = false;

  async connect() {
    const serial = (navigator as unknown as { serial?: SerialLike }).serial;
    if (!serial) throw new Error('Web Serial wird von diesem Browser nicht unterstützt (Chrome/Edge verwenden).');
    this.port = await serial.requestPort({});
    await this.port.open({ baudRate: 115200 });
    this.writer = this.port.writable!.getWriter();
    this.closed = false;
    void this.readLoop();
  }

  private async readLoop() {
    this.reader = this.port!.readable!.getReader();
    try {
      while (!this.closed) {
        const { value, done } = await this.reader.read();
        if (done) break;
        if (value) {
          const next = new Uint8Array(this.buf.length + value.length);
          next.set(this.buf);
          next.set(value, this.buf.length);
          this.buf = next;
          this.parse();
        }
      }
    } catch (err) {
      this.pending?.reject(err instanceof Error ? err : new Error(String(err)));
    } finally {
      this.reader?.releaseLock();
    }
  }

  private parse() {
    for (;;) {
      const start = this.buf.indexOf(0x24); // '$'
      if (start < 0) {
        this.buf = new Uint8Array(0);
        return;
      }
      if (start > 0) this.buf = this.buf.subarray(start);
      if (this.buf.length < 3) return;
      const ver = this.buf[1];
      const dir = this.buf[2];
      if ((ver !== 0x58 && ver !== 0x4d) || (dir !== 0x3e && dir !== 0x21)) {
        this.buf = this.buf.subarray(1);
        continue;
      }
      let cmd: number, len: number, headerLen: number, ok: boolean;
      if (ver === 0x58) { // 'X' – MSP v2
        if (this.buf.length < 8) return;
        cmd = this.buf[4] | (this.buf[5] << 8);
        len = this.buf[6] | (this.buf[7] << 8);
        headerLen = 8;
        if (this.buf.length < headerLen + len + 1) return;
        let crc = 0;
        for (let i = 3; i < headerLen + len; i++) crc = crc8(crc, this.buf[i]);
        ok = crc === this.buf[headerLen + len];
      } else { // 'M' – MSP v1
        if (this.buf.length < 5) return;
        len = this.buf[3];
        cmd = this.buf[4];
        headerLen = 5;
        if (this.buf.length < headerLen + len + 1) return;
        let x = 0;
        for (let i = 3; i < headerLen + len; i++) x ^= this.buf[i];
        ok = x === this.buf[headerLen + len];
      }
      const payload = this.buf.slice(headerLen, headerLen + len);
      this.buf = this.buf.subarray(headerLen + len + 1);
      const p = this.pending;
      if (p && p.cmd === cmd) {
        this.pending = null;
        if (!ok) p.reject(new Error('MSP Prüfsummenfehler'));
        else if (dir === 0x21) p.reject(new Error(`FC meldet Fehler für MSP-Befehl ${cmd}`));
        else p.resolve(payload);
      }
    }
  }

  request(cmd: number, payload: number[] = [], timeoutMs = 1500): Promise<Uint8Array> {
    if (!this.writer) return Promise.reject(new Error('Nicht verbunden'));
    const frame = [0x24, 0x58, 0x3c, 0, cmd & 0xff, cmd >> 8, payload.length & 0xff, payload.length >> 8, ...payload];
    let crc = 0;
    for (let i = 3; i < frame.length; i++) crc = crc8(crc, frame[i]);
    frame.push(crc);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending?.cmd === cmd) this.pending = null;
        reject(new Error('Zeitüberschreitung (keine Antwort vom FC)'));
      }, timeoutMs);
      this.pending = {
        cmd,
        resolve: (d) => { clearTimeout(timer); resolve(d); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      };
      this.writer!.write(new Uint8Array(frame)).catch((e) => {
        clearTimeout(timer);
        this.pending = null;
        reject(e);
      });
    });
  }

  async identify(): Promise<string> {
    const variant = new TextDecoder().decode(await this.request(MSP_FC_VARIANT));
    let version = '';
    try {
      const v = await this.request(MSP_FC_VERSION);
      version = `${v[0]}.${v[1]}.${v[2]}`;
    } catch { /* optional */ }
    return `${variant} ${version}`.trim();
  }

  async flashSummary(): Promise<FlashSummary> {
    const d = await this.request(MSP_DATAFLASH_SUMMARY);
    const dv = new DataView(d.buffer, d.byteOffset, d.byteLength);
    return {
      ready: !!(d[0] & 1),
      supported: !!(d[0] & 2),
      totalSize: d.length >= 9 ? dv.getUint32(5, true) : 0,
      usedSize: d.length >= 13 ? dv.getUint32(9, true) : 0,
    };
  }

  async hasSdCard(): Promise<boolean> {
    try {
      const d = await this.request(MSP_SDCARD_SUMMARY);
      return !!(d[0] & 1);
    } catch {
      return false;
    }
  }

  /** Reads `size` bytes of flash. Calls onProgress(bytesRead). Supports abort via signal. */
  async readFlash(size: number, onProgress: (n: number) => void, signal?: AbortSignal): Promise<Uint8Array> {
    const out = new Uint8Array(size);
    let addr = 0;
    const block = 4096;
    while (addr < size) {
      if (signal?.aborted) throw new Error('Abgebrochen');
      const want = Math.min(block, size - addr);
      let chunk: Uint8Array | null = null;
      for (let attempt = 0; attempt < 4 && !chunk; attempt++) {
        try {
          const d = await this.request(MSP_DATAFLASH_READ, [addr & 0xff, (addr >> 8) & 0xff, (addr >> 16) & 0xff, (addr >>> 24) & 0xff, want & 0xff, want >> 8, 0], 3000);
          const dv = new DataView(d.buffer, d.byteOffset, d.byteLength);
          const at = dv.getUint32(0, true);
          if (at !== addr) continue;
          // extended format: addr u32, dataSize u16, compression u8, data
          if (d.length >= 7) {
            const dataSize = dv.getUint16(4, true);
            if (d.length === 7 + dataSize && d[6] === 0) {
              chunk = d.subarray(7);
              break;
            }
          }
          chunk = d.subarray(4); // legacy format
        } catch (err) {
          if (attempt === 3) throw err;
        }
      }
      if (!chunk || !chunk.length) throw new Error(`Lesen bei Adresse ${addr} fehlgeschlagen`);
      const n = Math.min(chunk.length, size - addr);
      out.set(chunk.subarray(0, n), addr);
      addr += n;
      onProgress(addr);
    }
    return out;
  }

  async disconnect() {
    this.closed = true;
    try { await this.reader?.cancel(); } catch { /* ignore */ }
    try { this.writer?.releaseLock(); } catch { /* ignore */ }
    try { await this.port?.close(); } catch { /* ignore */ }
    this.port = null;
    this.writer = null;
    this.reader = null;
  }
}
