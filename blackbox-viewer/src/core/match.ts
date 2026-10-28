// Duel scoring shared by the single viewer (submitting a flight to a match), the VS viewer and the
// server-side views: only tricks inside a fixed window after take-off count, re-scored on their own
// (combos inside the window only), so a longer flight does not win automatically.
import { scoreTricks, summarize, type Analysis, type DetectSettings, type ScoreSummary } from '../analysis/tricks.ts';
import type { FlightData, Trick } from '../types.ts';

/** Wertungszeiten (s), die ein Match haben kann */
export const MATCH_LIMITS = [60, 90, 120, 180];

/**
 * Take-off: first moment the throttle stays above 15 % for 0.3 s. Logs that start in the air (or
 * without throttle) fall back to one second before the first trick, at the latest the log start.
 */
export function takeoffTime(f: FlightData, analysis?: Analysis | null): number {
  const hold = Math.max(1, Math.round(0.3 * f.rate));
  let run = 0;
  for (let i = 0; i < f.t.length; i++) {
    run = f.throttle[i] > 0.15 ? run + 1 : 0;
    if (run >= hold) return Math.max(0, f.t[i - hold + 1]);
  }
  const first = analysis?.tricks[0];
  return first ? Math.max(0, first.start - 1) : 0;
}

export interface MatchScore {
  /** log time where the window starts (take-off) */
  start: number;
  end: number;
  limit: number;
  tricks: Trick[];
  summary: ScoreSummary;
}

/** Tricks inside [start, start + limit], scored again on their own. */
export function windowScore(analysis: Analysis, settings: DetectSettings, start: number, limit: number): MatchScore {
  const end = start + limit;
  const tricks = analysis.tricks
    .filter((t) => t.start >= start && t.end <= end)
    .map((t) => ({ ...t, angles: [...t.angles] as Trick['angles'], features: [...t.features] }));
  scoreTricks(tricks, settings);
  return { start, end, limit, tricks, summary: summarize(tricks) };
}

export function matchScore(f: FlightData, analysis: Analysis, settings: DetectSettings, limit: number): MatchScore {
  return windowScore(analysis, settings, takeoffTime(f, analysis), limit);
}

/** Minimal trick shape – works with analysed tricks and with the lists stored on the server. */
export interface ScoredTrick {
  name: string;
  start: number;
  end: number;
  points: number;
}

export interface LiveScore {
  total: number;
  count: number;
  last: ScoredTrick | null;
}

/** Score up to time t (tricks count once they are finished). Times relative to the same origin as t. */
export function liveScore(tricks: ScoredTrick[], t: number): LiveScore {
  let total = 0, count = 0;
  let last: ScoredTrick | null = null;
  for (const tr of tricks) {
    if (tr.end > t) continue;
    total += tr.points;
    count++;
    if (!last || tr.end > last.end) last = tr;
  }
  return { total, count, last };
}
