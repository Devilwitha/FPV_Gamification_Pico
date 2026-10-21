// Decoder for Betaflight / Cleanflight / INAV blackbox logs (.bbl / .bfl / .txt).
// Follows the reference implementation in betaflight/blackbox-tools (parser.c, decoders.c)
// and betaflight/blackbox-log-viewer (flightlog_parser.js).

export interface FrameDef {
  names: string[];
  signed: boolean[];
  predictor: number[];
  encoding: number[];
  count: number;
}

export interface RawEvent {
  type: 'sync_beep' | 'disarm' | 'flight_mode' | 'resume' | 'adjustment' | 'log_end';
  /** microseconds (log clock) */
  time: number;
  value?: number;
}

export interface DecodeStats {
  mainFrames: number;
  corrupt: number;
  slowFrames: number;
  gpsFrames: number;
}

export interface DecodeHandlers {
  onHeaders?: (headers: Record<string, string>, mainDef: FrameDef) => void;
  /** Called for every accepted main (I/P) frame. Buffer is reused – copy what you need. */
  onFrame: (frame: Float64Array) => void;
  onProgress?: (fraction: number) => void;
}

export interface DecodeResult {
  headers: Record<string, string>;
  mainDef: FrameDef;
  events: RawEvent[];
  stats: DecodeStats;
}

export interface LogSpan {
  start: number;
  end: number;
}

const LOG_START = 'H Product:Blackbox flight data recorder by Nicholas Sherlock';

const ENC_SIGNED_VB = 0;
const ENC_UNSIGNED_VB = 1;
const ENC_NEG_14BIT = 3;
const ENC_TAG8_8SVB = 6;
const ENC_TAG2_3S32 = 7;
const ENC_TAG8_4S16 = 8;
const ENC_NULL = 9;
const ENC_TAG2_3SVARIABLE = 10;

const PRED_ZERO = 0;
const PRED_PREVIOUS = 1;
const PRED_STRAIGHT_LINE = 2;
const PRED_AVERAGE_2 = 3;
const PRED_MINTHROTTLE = 4;
const PRED_MOTOR_0 = 5;
const PRED_INC = 6;
const PRED_HOME_COORD = 7;
const PRED_1500 = 8;
const PRED_VBATREF = 9;
const PRED_LAST_MAIN_FRAME_TIME = 10;
const PRED_MINMOTOR = 11;
const PRED_HOME_COORD_1 = 256;

const EVENT_SYNC_BEEP = 0;
const EVENT_INFLIGHT_ADJUSTMENT = 13;
const EVENT_LOGGING_RESUME = 14;
const EVENT_DISARM = 15;
const EVENT_FLIGHT_MODE = 30;
const EVENT_LOG_END = 255;

const MAX_FRAME_LENGTH = 256;
const MAX_TIME_JUMP = 10 * 1_000_000;
const MAX_ITERATION_JUMP = 500 * 10;
const IDX_ITERATION = 0;
const IDX_TIME = 1;

const signExtend = (v: number, bits: number) => (v << (32 - bits)) >> (32 - bits);

class Stream {
  data: Uint8Array;
  pos: number;
  end: number;
  eof = false;

  constructor(data: Uint8Array, pos: number, end: number) {
    this.data = data;
    this.pos = pos;
    this.end = end;
  }

  readByte(): number {
    if (this.pos < this.end) return this.data[this.pos++];
    this.eof = true;
    return -1;
  }

  readUVB(): number {
    let result = 0;
    for (let i = 0, shift = 0; i < 5; i++, shift += 7) {
      const b = this.readByte();
      if (b < 0) return 0;
      result |= (b & 0x7f) << shift;
      if (b < 0x80) return result >>> 0;
    }
    return 0; // > 32 bit: corrupt
  }

  readSVB(): number {
    const u = this.readUVB();
    return (u >>> 1) ^ -(u & 1);
  }

  readU32(): number {
    const a = this.readByte(), b = this.readByte(), c = this.readByte(), d = this.readByte();
    return (a | (b << 8) | (c << 16) | (d << 24)) >>> 0;
  }

  tag8_8svb(values: Float64Array, count: number) {
    if (count === 1) {
      values[0] = this.readSVB();
      return;
    }
    let header = this.readByte();
    for (let i = 0; i < 8; i++, header >>= 1) values[i] = header & 1 ? this.readSVB() : 0;
  }

  tag2_3s32(values: Float64Array) {
    let lead = this.readByte();
    switch (lead >> 6) {
      case 0:
        values[0] = signExtend((lead >> 4) & 3, 2);
        values[1] = signExtend((lead >> 2) & 3, 2);
        values[2] = signExtend(lead & 3, 2);
        break;
      case 1:
        values[0] = signExtend(lead & 0x0f, 4);
        lead = this.readByte();
        values[1] = signExtend(lead >> 4, 4);
        values[2] = signExtend(lead & 0x0f, 4);
        break;
      case 2:
        values[0] = signExtend(lead & 0x3f, 6);
        values[1] = signExtend(this.readByte() & 0x3f, 6);
        values[2] = signExtend(this.readByte() & 0x3f, 6);
        break;
      default:
        this.wideFields(lead, values);
    }
  }

  tag2_3svariable(values: Float64Array) {
    const lead = this.readByte();
    switch (lead >> 6) {
      case 0:
        values[0] = signExtend((lead >> 4) & 3, 2);
        values[1] = signExtend((lead >> 2) & 3, 2);
        values[2] = signExtend(lead & 3, 2);
        break;
      case 1: {
        const b1 = this.readByte();
        values[0] = signExtend((lead & 0x3e) >> 1, 5);
        values[1] = signExtend(((lead & 0x01) << 4) | ((b1 & 0xf0) >> 4), 5);
        values[2] = signExtend(b1 & 0x0f, 4);
        break;
      }
      case 2: {
        const b1 = this.readByte();
        const b2 = this.readByte();
        values[0] = signExtend(((lead & 0x3f) << 2) | ((b1 & 0xc0) >> 6), 8);
        values[1] = signExtend(((b1 & 0x3f) << 1) | ((b2 & 0x80) >> 7), 7);
        values[2] = signExtend(b2 & 0x7f, 7);
        break;
      }
      default:
        this.wideFields(lead, values);
    }
  }

  private wideFields(selector: number, values: Float64Array) {
    for (let i = 0; i < 3; i++, selector >>= 2) {
      switch (selector & 3) {
        case 0:
          values[i] = signExtend(this.readByte(), 8);
          break;
        case 1: {
          const a = this.readByte(), b = this.readByte();
          values[i] = signExtend(a | (b << 8), 16);
          break;
        }
        case 2: {
          const a = this.readByte(), b = this.readByte(), c = this.readByte();
          values[i] = signExtend(a | (b << 8) | (c << 16), 24);
          break;
        }
        default: {
          const a = this.readByte(), b = this.readByte(), c = this.readByte(), d = this.readByte();
          values[i] = a | (b << 8) | (c << 16) | (d << 24);
        }
      }
    }
  }

  tag8_4s16_v1(values: Float64Array) {
    let selector = this.readByte();
    for (let i = 0; i < 4; i++) {
      switch (selector & 3) {
        case 0:
          values[i] = 0;
          break;
        case 1: {
          const c = this.readByte();
          values[i] = signExtend(c & 0x0f, 4);
          i++;
          selector >>= 2;
          values[i] = signExtend(c >> 4, 4);
          break;
        }
        case 2:
          values[i] = signExtend(this.readByte(), 8);
          break;
        default: {
          const a = this.readByte(), b = this.readByte();
          values[i] = signExtend(a | (b << 8), 16);
        }
      }
      selector >>= 2;
    }
  }

  tag8_4s16_v2(values: Float64Array) {
    let selector = this.readByte();
    let nibble = 0;
    let buffer = 0;
    for (let i = 0; i < 4; i++, selector >>= 2) {
      switch (selector & 3) {
        case 0:
          values[i] = 0;
          break;
        case 1:
          if (nibble === 0) {
            buffer = this.readByte() & 0xff;
            values[i] = signExtend(buffer >> 4, 4);
            nibble = 1;
          } else {
            values[i] = signExtend(buffer & 0x0f, 4);
            nibble = 0;
          }
          break;
        case 2:
          if (nibble === 0) {
            values[i] = signExtend(this.readByte() & 0xff, 8);
          } else {
            let c1 = (buffer << 4) & 0xff;
            buffer = this.readByte() & 0xff;
            c1 |= buffer >> 4;
            values[i] = signExtend(c1, 8);
          }
          break;
        default:
          if (nibble === 0) {
            const a = this.readByte() & 0xff, b = this.readByte() & 0xff;
            values[i] = signExtend((a << 8) | b, 16);
          } else {
            const a = this.readByte() & 0xff, b = this.readByte() & 0xff;
            values[i] = signExtend(((buffer << 12) | (a << 4) | (b >> 4)) & 0xffff, 16);
            buffer = b;
          }
      }
    }
  }
}

/** Find all individual logs inside a file (flash dumps often contain several). */
export function findLogs(data: Uint8Array): LogSpan[] {
  const m = new TextEncoder().encode(LOG_START);
  const starts: number[] = [];
  outer: for (let i = 0; i <= data.length - m.length; i++) {
    if (data[i] !== 0x48) continue;
    for (let j = 1; j < m.length; j++) if (data[i + j] !== m[j]) continue outer;
    starts.push(i);
    i += m.length - 1;
  }
  return starts.map((start, k) => ({ start, end: k + 1 < starts.length ? starts[k + 1] : data.length }));
}

export function parseHeaders(data: Uint8Array, start: number, end: number) {
  const headers: Record<string, string> = {};
  let pos = start;
  while (pos + 1 < end && data[pos] === 0x48 && data[pos + 1] === 0x20) {
    let e = pos;
    while (e < end && data[e] !== 0x0a) e++;
    let line = '';
    for (let k = pos + 2; k < e; k++) line += String.fromCharCode(data[k]);
    const c = line.indexOf(':');
    if (c > 0) headers[line.slice(0, c)] = line.slice(c + 1).trim();
    pos = e + 1;
  }
  return { headers, dataStart: pos };
}

const ints = (s: string | undefined) => (s ? s.split(',').map((v) => parseInt(v, 10) || 0) : []);

function frameDef(h: Record<string, string>, type: string): FrameDef | null {
  const nameKey = type === 'P' ? 'I' : type;
  const names = h[`Field ${nameKey} name`];
  if (!names) return null;
  const list = names.split(',');
  const signed = ints(h[`Field ${nameKey} signed`]);
  const def: FrameDef = {
    names: list,
    signed: list.map((_, i) => signed[i] === 1),
    predictor: ints(h[`Field ${type} predictor`]),
    encoding: ints(h[`Field ${type} encoding`]),
    count: list.length,
  };
  if (type === 'G') {
    for (let i = 1; i < def.count; i++)
      if (def.predictor[i - 1] === PRED_HOME_COORD && def.predictor[i] === PRED_HOME_COORD) def.predictor[i] = PRED_HOME_COORD_1;
  }
  return def;
}

export function decodeLog(data: Uint8Array, start: number, end: number, handlers: DecodeHandlers): DecodeResult {
  const { headers, dataStart } = parseHeaders(data, start, end);
  const defI = frameDef(headers, 'I');
  const defP = frameDef(headers, 'P');
  if (!defI || !defP || defI.count === 0) throw new Error('Log-Header ohne Felddefinitionen (Field I name fehlt)');
  const defS = frameDef(headers, 'S');
  const defG = frameDef(headers, 'G');
  const defH = frameDef(headers, 'H');
  handlers.onHeaders?.(headers, defI);

  // --- system config used by predictors ---
  const num = (k: string, d: number) => {
    const v = parseInt(headers[k] ?? '', 10);
    return Number.isFinite(v) ? v : d;
  };
  const dataVersion = num('Data version', 2);
  const minthrottle = num('minthrottle', 1150);
  const vbatref = num('vbatref', 4095);
  const motorOutputLow = headers['motorOutput'] ? ints(headers['motorOutput'])[0] : num('minmotor', minthrottle);
  const frameIntervalI = Math.max(1, num('I interval', 32));
  let pNum = 1;
  let pDenom = 1;
  const pInterval = headers['P interval'];
  if (pInterval) {
    const m = pInterval.match(/(\d+)\s*\/\s*(\d+)/);
    if (m) {
      pNum = parseInt(m[1], 10) || 1;
      pDenom = parseInt(m[2], 10) || 1;
    } else {
      pDenom = Math.max(1, parseInt(pInterval, 10) || 1);
    }
  }
  const motor0 = defI.names.indexOf('motor[0]');
  const hHome0 = defH ? defH.names.indexOf('GPS_home[0]') : -1;
  const hHome1 = defH ? defH.names.indexOf('GPS_home[1]') : -1;

  const n = defI.count;
  const ring = [new Float64Array(n), new Float64Array(n), new Float64Array(n)];
  let cur = ring[0];
  let prev: Float64Array | null = null;
  let prev2: Float64Array | null = null;
  const nextBuffer = () => ring.find((b) => b !== prev && b !== prev2) ?? ring[0];

  const slowCur = defS ? new Float64Array(defS.count) : null;
  const gCur = defG ? new Float64Array(defG.count) : null;
  let gLast = defG ? new Float64Array(defG.count) : null;
  const hCur = defH ? new Float64Array(defH.count) : null;
  const home = defH ? new Float64Array(defH.count) : null;
  let homeValid = false;

  let mainValid = false;
  let lastIter = -1;
  let lastTime = -1;
  let rollover = 0;
  let pendingEvent: RawEvent | null = null;
  let resume: { iter: number; time: number } | null = null;
  const events: RawEvent[] = [];
  const stats: DecodeStats = { mainFrames: 0, corrupt: 0, slowFrames: 0, gpsFrames: 0 };
  const vals = new Float64Array(8);
  const s = new Stream(data, dataStart, end);

  function predict(
    i: number, predictor: number, value: number, signed: boolean,
    current: Float64Array, previous: Float64Array | null, previous2: Float64Array | null,
  ): number {
    switch (predictor) {
      case PRED_ZERO:
        return value;
      case PRED_PREVIOUS:
        return previous ? value + previous[i] : value;
      case PRED_STRAIGHT_LINE:
        return previous && previous2 ? value + 2 * previous[i] - previous2[i] : value;
      case PRED_AVERAGE_2:
        if (!previous || !previous2) return value;
        return value + (signed ? Math.trunc((previous[i] + previous2[i]) / 2) : Math.floor((previous[i] + previous2[i]) / 2));
      case PRED_MINTHROTTLE:
        return value + minthrottle;
      case PRED_MOTOR_0:
        return motor0 >= 0 ? value + current[motor0] : value;
      case PRED_HOME_COORD:
        return home && hHome0 >= 0 ? value + home[hHome0] : value;
      case PRED_HOME_COORD_1:
        return home && hHome1 >= 0 ? value + home[hHome1] : value;
      case PRED_1500:
        return value + 1500;
      case PRED_VBATREF:
        return value + vbatref;
      case PRED_LAST_MAIN_FRAME_TIME:
        return prev ? value + prev[IDX_TIME] : value;
      case PRED_MINMOTOR:
        return value + motorOutputLow;
      default:
        return value;
    }
  }

  function parseFrame(def: FrameDef, current: Float64Array, previous: Float64Array | null, previous2: Float64Array | null, skipped: number) {
    const { predictor, encoding, signed, count } = def;
    let i = 0;
    while (i < count) {
      if (predictor[i] === PRED_INC) {
        current[i] = skipped + 1 + (previous ? previous[i] : 0);
        i++;
        continue;
      }
      let group = 0;
      switch (encoding[i]) {
        case ENC_SIGNED_VB:
          vals[0] = s.readSVB();
          group = 1;
          break;
        case ENC_UNSIGNED_VB:
          vals[0] = s.readUVB();
          group = 1;
          break;
        case ENC_NEG_14BIT:
          vals[0] = -signExtend(s.readUVB() & 0x3fff, 14);
          group = 1;
          break;
        case ENC_TAG8_4S16:
          if (dataVersion < 2) s.tag8_4s16_v1(vals);
          else s.tag8_4s16_v2(vals);
          group = 4;
          break;
        case ENC_TAG2_3S32:
          s.tag2_3s32(vals);
          group = 3;
          break;
        case ENC_TAG2_3SVARIABLE:
          s.tag2_3svariable(vals);
          group = 3;
          break;
        case ENC_TAG8_8SVB: {
          let j = i + 1;
          for (; j < i + 8 && j < count; j++) if (encoding[j] !== ENC_TAG8_8SVB) break;
          group = j - i;
          s.tag8_8svb(vals, group);
          break;
        }
        case ENC_NULL:
          vals[0] = 0;
          group = 1;
          break;
        default:
          throw new Error(`Nicht unterstützte Feldkodierung ${encoding[i]}`);
      }
      for (let j = 0; j < group && i < count; j++, i++) {
        current[i] = predict(i, predictor[i], vals[j], signed[i], current, previous, previous2);
      }
    }
  }

  const shouldHaveFrame = (idx: number) => ((idx % frameIntervalI) + pNum - 1) % pDenom < pNum;
  function skippedFrames(): number {
    if (lastIter < 0) return 0;
    let c = 0;
    for (let idx = lastIter + 1; !shouldHaveFrame(idx) && c < 1000; idx++) c++;
    return c;
  }

  function applyRollover() {
    let t = cur[IDX_TIME] >>> 0;
    if (lastTime >= 0) {
      const lastU = lastTime % 0x100000000;
      if (t < lastU && ((t - lastU) >>> 0) < MAX_TIME_JUMP) rollover += 0x100000000;
    }
    cur[IDX_TIME] = t + rollover;
  }

  function invalidate() {
    mainValid = false;
    prev = null;
    prev2 = null;
  }

  function emitMain() {
    stats.mainFrames++;
    handlers.onFrame(cur);
  }

  function parseEvent() {
    const type = s.readByte();
    pendingEvent = null;
    resume = null;
    switch (type) {
      case EVENT_SYNC_BEEP:
        pendingEvent = { type: 'sync_beep', time: s.readUVB() + rollover };
        break;
      case EVENT_INFLIGHT_ADJUSTMENT: {
        const fn = s.readByte();
        const value = fn > 127 ? s.readU32() : s.readSVB();
        pendingEvent = { type: 'adjustment', time: lastTime, value };
        break;
      }
      case EVENT_LOGGING_RESUME: {
        const iter = s.readUVB();
        const time = s.readUVB() + rollover;
        resume = { iter, time };
        pendingEvent = { type: 'resume', time };
        break;
      }
      case EVENT_DISARM:
        pendingEvent = { type: 'disarm', time: lastTime, value: s.readUVB() };
        break;
      case EVENT_FLIGHT_MODE: {
        const flags = s.readUVB();
        s.readUVB();
        pendingEvent = { type: 'flight_mode', time: lastTime, value: flags };
        break;
      }
      case EVENT_LOG_END: {
        const msg = 'End of log\0';
        let ok = s.end - s.pos >= msg.length;
        if (ok) for (let k = 0; k < msg.length; k++) if (s.readByte() !== msg.charCodeAt(k)) ok = false;
        if (ok) {
          s.end = s.pos;
          pendingEvent = { type: 'log_end', time: lastTime };
        }
        break;
      }
      default:
        break;
    }
  }

  function complete(marker: number) {
    switch (marker) {
      case 0x49: { // I
        applyRollover();
        const iter = cur[IDX_ITERATION];
        const t = cur[IDX_TIME];
        const ok = lastIter < 0 || (iter >= lastIter && iter < lastIter + MAX_ITERATION_JUMP && t >= lastTime && t < lastTime + MAX_TIME_JUMP);
        if (!ok) {
          invalidate();
          return;
        }
        mainValid = true;
        lastIter = iter;
        lastTime = t;
        emitMain();
        prev = cur;
        prev2 = cur;
        cur = nextBuffer();
        return;
      }
      case 0x50: { // P
        if (!mainValid) return;
        applyRollover();
        const iter = cur[IDX_ITERATION];
        const t = cur[IDX_TIME];
        if (t > lastTime + MAX_TIME_JUMP || iter > lastIter + MAX_ITERATION_JUMP) {
          invalidate();
          return;
        }
        lastIter = iter;
        lastTime = t;
        emitMain();
        prev2 = prev;
        prev = cur;
        cur = nextBuffer();
        return;
      }
      case 0x45: // E
        if (resume) {
          lastIter = resume.iter;
          lastTime = resume.time;
        }
        if (pendingEvent) events.push(pendingEvent);
        return;
      case 0x53: // S
        stats.slowFrames++;
        return;
      case 0x47: // G
        if (homeValid && gCur && gLast) {
          stats.gpsFrames++;
          gLast = gCur.slice();
        }
        return;
      case 0x48: // H
        if (hCur && home) {
          home.set(hCur);
          homeValid = true;
        }
        return;
    }
  }

  function parse(marker: number) {
    switch (marker) {
      case 0x49:
        parseFrame(defI!, cur, prev, null, 0);
        return;
      case 0x50:
        parseFrame(defP!, cur, prev, prev2, skippedFrames());
        return;
      case 0x45:
        parseEvent();
        return;
      case 0x53:
        parseFrame(defS!, slowCur!, null, null, 0);
        return;
      case 0x47:
        parseFrame(defG!, gCur!, gLast, null, 0);
        return;
      case 0x48:
        parseFrame(defH!, hCur!, null, null, 0);
        return;
    }
  }

  const isMarker = (c: number) =>
    c === 0x49 || c === 0x50 || c === 0x45 ||
    (c === 0x53 && !!defS) || (c === 0x47 && !!defG) || (c === 0x48 && !!defH);

  let lastMarker = -1;
  let frameStart = 0;
  let frameEnd = 0;
  let prematureEof = false;
  let nextProgress = dataStart;
  const total = Math.max(1, end - dataStart);

  for (;;) {
    const command = s.readByte();
    if (lastMarker >= 0) {
      const looksComplete = isMarker(command) || (!prematureEof && command === -1);
      if (!prematureEof && looksComplete && frameEnd - frameStart <= MAX_FRAME_LENGTH) {
        complete(lastMarker);
      } else {
        // corrupt frame: resync starting one byte after its start
        invalidate();
        stats.corrupt++;
        s.pos = frameStart + 1;
        s.eof = false;
        lastMarker = -1;
        prematureEof = false;
        continue;
      }
    }
    if (command === -1) break;
    if (handlers.onProgress && s.pos >= nextProgress) {
      handlers.onProgress((s.pos - dataStart) / total);
      nextProgress = s.pos + 256 * 1024;
    }
    frameStart = s.pos - 1;
    if (isMarker(command)) {
      lastMarker = command;
      parse(command);
      frameEnd = s.pos;
      if (s.eof) prematureEof = true;
    } else {
      mainValid = false;
      lastMarker = -1;
    }
  }

  return { headers, mainDef: defI, events, stats };
}
