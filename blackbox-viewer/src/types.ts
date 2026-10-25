export type Vec3Arr = [Float32Array, Float32Array, Float32Array];

export interface LogEvent {
  /** seconds relative to log start */
  time: number;
  type: 'sync_beep' | 'disarm' | 'flight_mode' | 'resume' | 'adjustment' | 'log_end';
  value?: number;
}

export interface LogInfo {
  source: 'bbl' | 'csv' | 'demo';
  fileName: string;
  firmware: string;
  craft: string;
  frames: number;
  srcRate: number;
  duration: number;
  fields: string[];
  headers: Record<string, string>;
  logIndex: number;
  logCount: number;
  corruptFrames: number;
  /** battery voltage at the start of the log (V), null if not logged */
  vbat: number | null;
  /** estimated cell count (1, 2, 3, 4, 6, 8), null if unknown */
  cells: number | null;
}

/** Processed flight data at a uniform-ish rate (≈500 Hz). */
export interface FlightData {
  /** seconds from log start */
  t: Float64Array;
  rate: number;
  /** body rates in deg/s: roll, pitch, yaw */
  gyro: Vec3Arr;
  /** accelerometer in g, or null */
  acc: Vec3Arr | null;
  /** 0..1 */
  throttle: Float32Array;
  /** roll, pitch, yaw stick, -1..1 */
  sticks: Vec3Arr;
  /** mean motor output 0..1 */
  motor: Float32Array | null;
  /** attitude quaternion, n*4 (w,x,y,z), body→world */
  quat: Float32Array;
  events: LogEvent[];
  info: LogInfo;
}

/** Intermediate series before decimation / orientation. */
export interface RawSeries {
  /** seconds, absolute */
  t: Float64Array;
  gyro: Vec3Arr;
  acc: Vec3Arr | null;
  /** divide acc by this to get g; null = auto-detect */
  accScale: number | null;
  throttle: Float32Array;
  sticks: Vec3Arr;
  motor: Float32Array | null;
  /** battery voltage at the start of the log (V), null if not logged */
  vbatStart: number | null;
}

export type BuiltinTrickType =
  | 'roll'
  | 'flip'
  | 'powerloop'
  | 'splits'
  | 'immelmann'
  | 'yawspin'
  | 'corkscrew'
  | 'mattyflip'
  | 'helix'
  | 'trippyspin'
  | 'orbit'
  | 'invertedhang'
  | 'dive'
  | 'none';

/** Built-in trick or a community trick from the server ("c<id>", see analysis/learned.ts). */
export type TrickType = BuiltinTrickType | (string & {});

export interface Trick {
  id: number;
  /** stable key for user overrides (start time) */
  key: string;
  type: TrickType;
  name: string;
  detail: string;
  count: number;
  start: number;
  end: number;
  /** integrated body angles over the span, deg (signed) */
  angles: [number, number, number];
  duration: number;
  meanThrottle: number;
  peakRate: number;
  purity: number;
  basePoints: number;
  comboLevel: number;
  comboMult: number;
  /** 0 = first of its kind, 1 = same trick directly again, 2 = third in a row … */
  repeat: number;
  /** point factor for repeats: 1, 0.5, 0.25, 0.125 */
  repeatMult: number;
  points: number;
  source: 'rules' | 'ml' | 'user' | 'community';
  features: number[];
  /** axis whose angle drives the live progress gauge */
  axis: 0 | 1 | 2;
}

// ---- worker protocol ----
export interface LogSummary {
  index: number;
  bytes: number;
}

export type WorkerRequest =
  | { type: 'load'; name: string; buffer: ArrayBuffer }
  | { type: 'process'; index: number }
  | { type: 'csv' };

export type WorkerResponse =
  | { type: 'logs'; logs: LogSummary[]; source: 'bbl' | 'csv' }
  | { type: 'progress'; value: number; label: string }
  | { type: 'flight'; flight: FlightData }
  | { type: 'csv'; blob: Blob; name: string }
  | { type: 'error'; message: string };
