// FPV sim core: rigid body, BLDC motors, battery, prop aero, Betaflight-style FC.
// Frame convention (matches three.js): body +X right, +Y up, -Z forward.
// FC axes: roll (right wing down +), pitch (nose up +), yaw (nose right +), all deg/s.
(function (G) {
  'use strict';
  const D2R = Math.PI / 180, R2D = 180 / Math.PI, TAU = Math.PI * 2;
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

  // ---------- vector / quaternion helpers (arrays) ----------
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const scl = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const len = (a) => Math.hypot(a[0], a[1], a[2]);
  function qRot(q, v) {
    const w = q[0], x = q[1], y = q[2], z = q[3];
    const tx = 2 * (y * v[2] - z * v[1]), ty = 2 * (z * v[0] - x * v[2]), tz = 2 * (x * v[1] - y * v[0]);
    return [v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx)];
  }
  const qConj = (q) => [q[0], -q[1], -q[2], -q[3]];
  const qRotInv = (q, v) => qRot(qConj(q), v);
  function qMul(a, b) {
    return [
      a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
      a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
      a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
      a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
    ];
  }
  function qNorm(q) { const l = Math.hypot(q[0], q[1], q[2], q[3]); return [q[0] / l, q[1] / l, q[2] / l, q[3] / l]; }
  const qAxis = (ax, ang) => { const s = Math.sin(ang / 2); return [Math.cos(ang / 2), ax[0] * s, ax[1] * s, ax[2] * s]; };
  function gauss() { let u = 0, v = 0; while (u === 0) u = Math.random(); v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(TAU * v); }

  // ---------- filters ----------
  class PT1 {
    constructor(hz, dt) { this.y = 0; this.set(hz, dt); }
    set(hz, dt) { const rc = 1 / (TAU * hz); this.k = dt / (rc + dt); }
    update(x) { this.y += this.k * (x - this.y); return this.y; }
  }
  class Notch { // RBJ biquad notch, transposed direct form II
    constructor() { this.s1 = 0; this.s2 = 0; this.b0 = 1; this.b1 = 0; this.b2 = 0; this.a1 = 0; this.a2 = 0; }
    set(f, fs, q) {
      const w = TAU * f / fs, c = Math.cos(w), al = Math.sin(w) / (2 * q), a0 = 1 + al;
      this.b0 = 1 / a0; this.b1 = -2 * c / a0; this.b2 = 1 / a0; this.a1 = -2 * c / a0; this.a2 = (1 - al) / a0;
    }
    update(x) {
      const y = this.b0 * x + this.s1;
      this.s1 = this.b1 * x - this.a1 * y + this.s2;
      this.s2 = this.b2 * x - this.a2 * y;
      return y;
    }
  }

  // ---------- Betaflight rate curves (stick -1..1 -> deg/s) ----------
  function applyRates(type, x, r) {
    const ax = Math.abs(x);
    let a;
    if (type === 'actual') {
      const e = ax * (Math.pow(x, 5) * r.expo + x * (1 - r.expo));
      a = x * r.center + Math.max(0, r.max - r.center) * e;
    } else if (type === 'quick') {
      const rcRate = r.rate * 200, maxDPS = Math.max(r.max, rcRate), k = maxDPS / rcRate;
      const sfc = (k - 1) / k;
      const curve = ax * ax * ax * r.expo + ax * (1 - r.expo);
      a = x * rcRate / clamp(1 - curve * sfc, 0.01, 1);
    } else { // betaflight
      let c = x;
      if (r.expo) c = x * ax * ax * ax * r.expo + x * (1 - r.expo);
      let rc = r.rate; if (rc > 2) rc += (rc - 2) * 14.54;
      a = 200 * rc * c;
      if (r.srate) a /= clamp(1 - ax * r.srate, 0.01, 1);
    }
    return clamp(a, -1998, 1998);
  }

  // ---------- LiPo / Li-ion open-circuit voltage per cell vs state of charge ----------
  const OCV = {
    lipo: [[0, 3.27], [0.05, 3.5], [0.1, 3.62], [0.2, 3.7], [0.4, 3.77], [0.6, 3.85], [0.8, 3.98], [0.9, 4.08], [1, 4.2]],
    liion: [[0, 3.0], [0.05, 3.2], [0.1, 3.35], [0.2, 3.48], [0.4, 3.63], [0.6, 3.78], [0.8, 3.95], [0.9, 4.06], [1, 4.2]],
  };
  function ocv(chem, soc) {
    const t = OCV[chem]; soc = clamp(soc, 0, 1);
    for (let i = 1; i < t.length; i++) if (soc <= t[i][0]) {
      const f = (soc - t[i - 1][0]) / (t[i][0] - t[i - 1][0]);
      return t[i - 1][1] + f * (t[i][1] - t[i - 1][1]);
    }
    return t[t.length - 1][1];
  }

  // ---------- Presets ----------
  // Prop coefficients are derived from a measured-style operating point:
  // static thrust per motor (N) at a loaded RPM and sagged pack voltage.
  const PRESETS = {
    whoop: {
      name: '65 mm Whoop · 1S', mass: 0.032, arm: 0.023, inertia: [1.7e-5, 2.9e-5, 1.6e-5],
      propD: 0.031, propJ: 3.5e-8, kv: 25000, rWind: 0.35, i0: 0.15,
      opThrust: 0.24, opRpm: 46000, cells: 1, chem: 'lipo', capAh: 0.3, rCell: 0.04, iMax: 7,
      cdA: [0.0026, 0.0042, 0.0022], rotorDrag: 1.2e-6, jT: 0.75, jQ: 1.0, ducted: true,
      pid: { roll: [60, 90, 45, 70], pitch: [62, 92, 48, 70], yaw: [60, 90, 0, 70] },
      camTilt: 15, hitR: 0.034, crashSpeed: 30,
    },
    toothpick: {
      name: '3.5" Toothpick · 4S', mass: 0.24, arm: 0.055, inertia: [4.6e-4, 8.4e-4, 4.2e-4],
      propD: 0.089, propJ: 1.5e-6, kv: 3800, rWind: 0.24, i0: 0.6,
      opThrust: 4.6, opRpm: 33000, cells: 4, chem: 'lipo', capAh: 0.65, rCell: 0.009, iMax: 25,
      cdA: [0.006, 0.0105, 0.0048], rotorDrag: 6e-6, jT: 0.85, jQ: 1.05,
      pid: { roll: [45, 80, 32, 120], pitch: [47, 84, 36, 125], yaw: [45, 80, 0, 120] },
      camTilt: 25, hitR: 0.045, crashSpeed: 14,
    },
    freestyle: {
      name: '5" Freestyle · 6S', mass: 0.68, arm: 0.08, inertia: [3.2e-3, 5.6e-3, 2.9e-3],
      propD: 0.1295, propJ: 8e-6, kv: 1950, rWind: 0.2, i0: 1.5,
      opThrust: 15, opRpm: 29000, cells: 6, chem: 'lipo', capAh: 1.3, rCell: 0.004, iMax: 50,
      cdA: [0.014, 0.022, 0.0105], rotorDrag: 1.6e-5, jT: 0.9, jQ: 1.1,
      pid: { roll: [45, 80, 30, 120], pitch: [47, 84, 34, 125], yaw: [45, 80, 0, 120] },
      camTilt: 30, hitR: 0.065, crashSpeed: 11,
    },
    longrange: {
      name: '7" Long Range · 6S Li-ion', mass: 1.0, arm: 0.12, inertia: [9e-3, 1.6e-2, 8.2e-3],
      propD: 0.178, propJ: 2.6e-5, kv: 1300, rWind: 0.16, i0: 1.2,
      opThrust: 17, opRpm: 19500, cells: 6, chem: 'liion', capAh: 3.0, rCell: 0.012, iMax: 30,
      cdA: [0.02, 0.034, 0.014], rotorDrag: 2.6e-5, jT: 0.95, jQ: 1.15,
      pid: { roll: [38, 50, 50, 40], pitch: [40, 52, 54, 45], yaw: [40, 60, 0, 40] },
      camTilt: 20, hitR: 0.09, crashSpeed: 10,
    },
  };

  function derivePreset(p) {
    const ke = 60 / (TAU * p.kv);           // V·s/rad, equals Kt in N·m/A
    const vOp = p.cells * 3.8;
    const w = p.opRpm * TAU / 60, n = w / TAU;
    const amps = (vOp - ke * w) / p.rWind;
    const tq = ke * (amps - p.i0);
    const rho = 1.225;
    return Object.assign({}, p, {
      ke,
      ct: p.opThrust / (rho * n * n * Math.pow(p.propD, 4)),
      cq: tq / (rho * n * n * Math.pow(p.propD, 5)),
      area: Math.PI * (p.propD / 2) ** 2,
    });
  }

  const DEFAULT_RATES = {
    type: 'actual',
    roll: { rate: 1.0, srate: 0.7, expo: 0, center: 70, max: 670 },
    pitch: { rate: 1.0, srate: 0.7, expo: 0, center: 70, max: 670 },
    yaw: { rate: 1.0, srate: 0.7, expo: 0, center: 70, max: 670 },
  };

  // ---------- Quad ----------
  class Quad {
    constructor(env, opts = {}) {
      this.env = env;
      this.dt = opts.dt || 1 / 2000;
      this.cfg = {
        rates: JSON.parse(JSON.stringify(DEFAULT_RATES)),
        idle: 0.055, tpaRate: 0.65, tpaBreak: 0.35, rcSmoothHz: 40,
        gyroLpfHz: 250, dtermLpfHz: 90, rpmFilter: true, gyroNoise: true, vibration: 1.0,
        propwash: 1.0, groundEffect: true, batterySag: true, crashDamage: true, damage: true,
        angleMax: 55, angleGain: 6, ...opts.cfg,
      };
      this.setPreset(opts.preset || 'freestyle');
      this.reset([0, 0.2, 0], 0);
    }

    setPreset(key) {
      this.presetKey = key;
      const p = this.p = derivePreset(PRESETS[key]);
      this.pid = JSON.parse(JSON.stringify(p.pid));
      const a = p.arm;
      // props-in default: FL CW, FR CCW, RR CW, RL CCW. spin +1 = CCW seen from above.
      this.motors = [
        { r: [a, 0.012, a], spin: -1 },   // M1 rear right
        { r: [a, 0.012, -a], spin: 1 },   // M2 front right
        { r: [-a, 0.012, a], spin: 1 },   // M3 rear left
        { r: [-a, 0.012, -a], spin: -1 }, // M4 front left
      ].map((m) => Object.assign(m, {
        rf: m.r[0] < 0 ? 1 : -1, pf: m.r[2] < 0 ? 1 : -1, yf: m.spin,
        w: 0, u: 0, thrust: 0, amps: 0, phase: Math.random() * TAU, wash: 0, washT: 0, dir: 1,
      }));
      this.invI = p.inertia.map((v) => 1 / v);
      const r = p.hitR, s = a / 0.08;
      this.hitPoints = [
        ...this.motors.map((m) => ({ b: [m.r[0], 0.01, m.r[2]], r })),
        { b: [0, 0.035 * s, 0.01], r: 0.03 * s },
        { b: [0, -0.022 * s, 0], r: 0.025 * s },
        { b: [0, 0.012 * s, -a * 0.8], r: 0.025 * s },
        { b: [0, 0.01 * s, a * 0.8], r: 0.025 * s },
      ];
      this._filters();
    }

    _filters() {
      const dt = this.dt, c = this.cfg;
      this.gyroLpf = [0, 1, 2].map(() => new PT1(c.gyroLpfHz, dt));
      this.dLpf = [0, 1, 2].map(() => new PT1(c.dtermLpfHz, dt));
      this.dLpf2 = [0, 1, 2].map(() => new PT1(c.dtermLpfHz * 1.6, dt));
      this.rcLpf = [0, 1, 2, 3].map(() => [new PT1(c.rcSmoothHz, dt), new PT1(c.rcSmoothHz, dt)]);
      this.relaxLpf = [0, 1, 2].map(() => new PT1(15, dt));
      this.notches = [0, 1, 2].map(() => [0, 1, 2, 3].map(() => new Notch()));
      this.vibAxis = [0, 1, 2, 3].map(() => [gauss(), gauss(), gauss() * 0.5]);
    }

    reset(pos, yaw) {
      this.pos = pos.slice();
      this.vel = [0, 0, 0];
      this.q = qAxis([0, 1, 0], yaw || 0);
      this.wb = [0, 0, 0];
      this.armed = false; this.crashed = false; this.turtle = false;
      this.soc = 1; this.vPol = 0; this.mah = 0; this.ampsTotal = 0;
      this.vbat = this.p.cells * 4.2;
      this.iterm = [0, 0, 0]; this.prevGyro = [0, 0, 0]; this.prevSp = [0, 0, 0];
      this.sp = [0, 0, 0]; this.gyro = [0, 0, 0]; this.pidOut = [0, 0, 0]; this.windup = 1;
      this.time = 0; this.flightTime = 0;
      this.events = [];
      this.maxG = 0;
      this.repair();
      for (const m of this.motors) { m.w = 0; m.u = 0; m.thrust = 0; m.amps = 0; }
      for (const f of this.gyroLpf.concat(this.dLpf, this.dLpf2, this.relaxLpf)) f.y = 0;
      for (const p of this.rcLpf) { p[0].y = 0; p[1].y = 0; }
    }

    // body-frame attitude angles for angle mode / OSD
    attitude() {
      const right = qRot(this.q, [1, 0, 0]), fwd = qRot(this.q, [0, 0, -1]), up = qRot(this.q, [0, 1, 0]);
      return {
        roll: Math.atan2(-right[1], up[1]) * R2D,
        pitch: Math.asin(clamp(fwd[1], -1, 1)) * R2D,
        heading: Math.atan2(fwd[0], -fwd[2]) * R2D,
        up, fwd, right,
      };
    }

    step(rc) {
      const dt = this.dt, p = this.p, c = this.cfg, env = this.env;
      this.time += dt;
      const rho = env.rho || 1.225;

      // ---- arming / modes ----
      if (rc.arm && !this.armed && !this.crashed && rc.thr < 0.06) { this.armed = true; this.turtle = !!rc.turtle; this.iterm = [0, 0, 0]; }
      if (!rc.arm && this.armed) this.armed = false;
      if (this.armed && this.turtle && !rc.turtle) this.turtle = false;

      // ---- RC smoothing (PT2) and rates ----
      const sticks = [rc.roll, rc.pitch, rc.yaw, rc.thr];
      const sm = sticks.map((v, i) => this.rcLpf[i][1].update(this.rcLpf[i][0].update(v)));
      const R = c.rates;
      let sp = [applyRates(R.type, sm[0], R.roll), applyRates(R.type, sm[1], R.pitch), applyRates(R.type, sm[2], R.yaw)];
      if (rc.angle) {
        const att = this.attitude();
        sp[0] = (sm[0] * c.angleMax - att.roll) * c.angleGain;
        sp[1] = (sm[1] * c.angleMax - att.pitch) * c.angleGain;
        sp[0] = clamp(sp[0], -500, 500); sp[1] = clamp(sp[1], -500, 500);
      }
      const throttle = clamp(sm[3], 0, 1);

      // ---- gyro: truth + noise + vibration, then RPM notches and LPF ----
      const wb = this.wb;
      const gTrue = [-wb[2] * R2D, wb[0] * R2D, -wb[1] * R2D];
      const fs = 1 / dt;
      for (let ax = 0; ax < 3; ax++) {
        let g = gTrue[ax];
        if (c.gyroNoise) {
          g += gauss() * 0.25;
          for (let i = 0; i < 4; i++) {
            const m = this.motors[i], wn = m.w / 3000;
            g += c.vibration * 9 * wn * wn * Math.sin(m.phase) * this.vibAxis[i][ax] * (1 + 12 * (m.imb || 0));
            // kaputter Propeller flattert auch breitbandig - das filtert der RPM-Filter nicht weg
            if (m.imb) g += c.vibration * 40 * m.imb * wn * wn * gauss();
          }
        }
        if (c.rpmFilter) {
          for (let i = 0; i < 4; i++) {
            const f = clamp(this.motors[i].w / TAU, 80, fs * 0.45);
            const nf = this.notches[ax][i];
            nf.set(f, fs, 5); g = nf.update(g);
          }
        }
        this.gyro[ax] = this.gyroLpf[ax].update(g);
      }

      // ---- PID (Betaflight scaling) ----
      const tpa = throttle > c.tpaBreak ? 1 - (throttle - c.tpaBreak) / (1 - c.tpaBreak) * c.tpaRate : 1;
      const axes = ['roll', 'pitch', 'yaw'], out = [0, 0, 0];
      for (let ax = 0; ax < 3; ax++) {
        const [kp, ki, kd, kf] = this.pid[axes[ax]];
        const err = sp[ax] - this.gyro[ax];
        const hp = sp[ax] - this.relaxLpf[ax].update(sp[ax]);
        const relax = (ax < 2 ? Math.max(0, 1 - Math.abs(hp) / 40) : 1) * this.windup;
        if (this.armed) this.iterm[ax] = clamp(this.iterm[ax] + ki * 0.244381 * err * dt * relax, -400, 400);
        else this.iterm[ax] = 0;
        const dG = (this.gyro[ax] - this.prevGyro[ax]) * fs;
        const dF = this.dLpf2[ax].update(this.dLpf[ax].update(dG));
        const ff = kf * 0.00013754 * (sp[ax] - this.prevSp[ax]) * fs;
        this.prevGyro[ax] = this.gyro[ax]; this.prevSp[ax] = sp[ax];
        const sum = kp * 0.032029 * err + this.iterm[ax] - kd * 0.000529 * dF * tpa + ff;
        out[ax] = clamp(sum, ax === 2 ? -400 : -500, ax === 2 ? 400 : 500) / 1000;
      }
      this.sp = sp; this.pidOut = out;

      // ---- mixer with airmode ----
      const mix = this.motors.map((m) => out[0] * m.rf + out[1] * m.pf + out[2] * m.yf);
      let mn = Math.min(...mix), mx = Math.max(...mix), thr = throttle;
      if (mx - mn > 1) { for (let i = 0; i < 4; i++) mix[i] /= (mx - mn); mn = Math.min(...mix); mx = Math.max(...mix); thr = 0.5; }
      thr = clamp(thr, -mn, 1 - mx);
      // iterm windup protection: freeze I as mixer range approaches saturation (BF iterm_windup 85)
      const range = mx - mn;
      this.windup = range > 0.85 ? clamp((1 - range) / 0.15, 0, 1) : 1;

      // ---- motors, ESC, battery ----
      const vb = c.batterySag ? this.vbat : p.cells * 4.0;
      let iBat = 0;
      const R0 = this.q, vRel = sub(this.vel, env.wind ? env.wind(this.time, this.pos) : [0, 0, 0]);
      const vBody = qRotInv(R0, vRel);
      const force = [0, 0, 0], torque = [0, 0, 0];
      const h = [0, 0, 0];
      const up = qRot(R0, [0, 1, 0]);
      const dm = this.damage, wRef = p.opRpm * TAU / 60;
      for (let i = 0; i < 4; i++) {
        const m = this.motors[i];
        if (dm.arm[i]) { // Arm gebrochen: Motor samt Propeller weg
          m.u = 0; m.w = 0; m.thrust = 0; m.amps = 0; m.wash = 0; m.imb = 0;
          continue;
        }
        let u = 0, dir = 1;
        if (!dm.power) u = 0; // Akkukabel abgerissen
        else if (this.armed && !this.turtle) u = c.idle + (1 - c.idle) * (thr + mix[i]);
        else if (this.armed && this.turtle) {
          const s = rc.roll * m.rf + rc.pitch * m.pf;
          u = clamp(s, 0, 1) * Math.min(1, Math.hypot(rc.roll, rc.pitch)) * 0.9; dir = -1;
        }
        m.u = u; m.dir = dir;
        // local airflow through the disc
        const vm = add(vBody, cross(wb, m.r));
        const n = Math.abs(m.w) / TAU;
        const nD = Math.max(n * p.propD, 2);
        const va = vm[1] * dir;
        const J = clamp(va / nD, -0.6, 1.3);
        let ctF = J >= 0 ? Math.max(0, 1 - J / p.jT) : Math.min(1.2, 1 - J * 0.35);
        let cqF = J >= 0 ? Math.max(0.05, 1 - J / p.jQ) : Math.min(1.15, 1 - J * 0.25);
        if (dir < 0) { ctF *= 0.55; cqF *= 0.8; }
        // electrical
        const wAbs = Math.abs(m.w);
        const vApp = u * vb;
        let amps = (u > 0 ? (vApp - p.ke * wAbs) / p.rWind : 0);
        if (u <= 0.001) amps = 0; // freewheel when disarmed
        amps = clamp(amps, -p.iMax, p.iMax); // ESC current limit
        const pd = dm.prop[i], md = dm.motor[i];
        // verbogener Motor: weniger Drehmoment, mehr Reibung; kaputter Propeller: weniger Last und Schub
        const tqM = (p.ke * amps - Math.sign(wAbs) * p.ke * p.i0 * (wAbs > 1 ? 1 : wAbs) * (1 + 3 * md)) * Math.max(0, 1 - 0.6 * md);
        const propEff = Math.pow(Math.max(0, 1 - pd), 1.4);
        const qProp = p.cq * cqF * rho * n * n * Math.pow(p.propD, 5) * (0.25 + 0.75 * propEff);
        let dw = (tqM - qProp) / p.propJ;
        m.w = Math.max(0, wAbs + dw * dt);
        m.amps = amps; iBat += amps * u;
        m.phase += m.w * dt; if (m.phase > TAU) m.phase -= TAU;
        // thrust with inflow, ground effect, propwash
        let T = p.ct * ctF * rho * n * n * Math.pow(p.propD, 4) * propEff;
        if (c.groundEffect && up[1] > 0.3 && env.groundH) {
          const wp = add(this.pos, qRot(R0, m.r));
          const hh = wp[1] - env.groundH(wp[0], wp[2]);
          const Rp = p.propD / 2, k = Rp / (4 * Math.max(hh, Rp * 0.5));
          if (hh < 4 * p.propD) T *= Math.min(1.3, 1 / (1 - k * k));
        }
        const vi = Math.sqrt(Math.max(T, 1e-4) / (2 * rho * p.area));
        const s = -va / Math.max(vi, 0.5);
        const wash = (s > 0.15 && s < 2.6) ? Math.exp(-(((s - 0.95) / 0.5) ** 2)) * c.propwash : 0;
        m.washT += (gauss() - m.washT) * clamp(dt * 140, 0, 1);
        m.wash = wash;
        T *= (1 - 0.22 * wash) * (1 + 0.9 * wash * m.washT);
        T = Math.max(0, T) * dir;
        m.thrust = T;
        // forces/torques in body
        force[1] += T;
        const tq = cross(m.r, [0, T, 0]);
        torque[0] += tq[0]; torque[1] += tq[1]; torque[2] += tq[2];
        torque[1] += -m.spin * tqM * dir;               // reaction torque (includes spool-up kick)
        // Unwucht (angeschlagener Propeller, verbogene Welle): Fliehkraft dreht mit dem Rotor
        m.imb = (pd > 0.98 ? 0 : 0.9 * pd) + 0.6 * md;
        if (m.imb > 0.005) {
          const fu = m.imb * 0.06 * p.mass * 9.81 * (m.w / wRef) ** 2;
          const fi = [fu * Math.cos(m.phase), 0, fu * Math.sin(m.phase)];
          force[0] += fi[0]; force[2] += fi[2];
          const tqi = cross(m.r, fi);
          torque[0] += tqi[0]; torque[1] += tqi[1]; torque[2] += tqi[2];
        }
        // rotor H-force (blade flapping drag) opposes in-plane flow
        const kH = p.rotorDrag * m.w;
        const fh = [-kH * vm[0], 0, -kH * vm[2]];
        force[0] += fh[0]; force[2] += fh[2];
        const tqh = cross(m.r, fh);
        torque[0] += tqh[0]; torque[1] += tqh[1]; torque[2] += tqh[2];
        h[1] += m.spin * p.propJ * m.w * dir;
      }
      // battery: OCV - I·Rint - polarization (RC branch)
      const rInt = p.rCell * p.cells * (1 + 3 * this.damage.battery);
      this.vPol += ((iBat * rInt * 0.45) - this.vPol) * dt / 4;
      this.soc -= iBat * dt / (p.capAh * 3600);
      this.mah += Math.max(0, iBat) * dt / 3.6;
      this.ampsTotal = iBat;
      this.vbat = this.damage.power ? Math.max(p.cells * 2.8, p.cells * ocv(p.chem, this.soc) - iBat * rInt - this.vPol) : 0;

      // frame drag (quadratic, directional)
      const sp2 = len(vBody);
      for (let i = 0; i < 3; i++) force[i] += -0.5 * rho * p.cdA[i] * vBody[i] * sp2;
      // rotational aero damping
      for (let i = 0; i < 3; i++) torque[i] += -p.mass * p.arm * p.arm * 0.02 * wb[i] * Math.abs(wb[i]) - p.inertia[i] * 0.1 * wb[i];
      // gyroscopic coupling of spinning props
      const gy = cross(wb, h);
      for (let i = 0; i < 3; i++) torque[i] -= gy[i];

      // ---- integrate rigid body (semi-implicit Euler) ----
      const fw = qRot(R0, force);
      const acc = [fw[0] / p.mass, fw[1] / p.mass - 9.81, fw[2] / p.mass];
      if (env.softAt) { // foliage: heavy drag and random knocks
        const soft = env.softAt(this.pos);
        if (soft) {
          for (let i = 0; i < 3; i++) acc[i] -= this.vel[i] * 7 * soft;
          for (let i = 0; i < 3; i++) wb[i] += gauss() * 40 * soft * dt;
          if (c.damage) for (let i = 0; i < 4; i++) {
            const m = this.motors[i];
            if (m.w > 300) this._hurtProp(i, soft * dt * 0.35 * Math.min(1.5, m.w / wRef) * this._fragility(), 'Laub');
          }
        }
      }
      this.vel = add(this.vel, scl(acc, dt));
      this.pos = add(this.pos, scl(this.vel, dt));
      const Iw = [p.inertia[0] * wb[0], p.inertia[1] * wb[1], p.inertia[2] * wb[2]];
      const cor = cross(wb, Iw);
      for (let i = 0; i < 3; i++) wb[i] += (torque[i] - cor[i]) * this.invI[i] * dt;
      const dq = qMul(this.q, [0, wb[0], wb[1], wb[2]]);
      this.q = qNorm([this.q[0] + 0.5 * dq[0] * dt, this.q[1] + 0.5 * dq[1] * dt, this.q[2] + 0.5 * dq[2] * dt, this.q[3] + 0.5 * dq[3] * dt]);
      const gLoad = len(sub(acc, [0, -9.81, 0])) / 9.81;
      this.gLoad = gLoad;

      this._collide();
      if (this.armed) this.flightTime += dt;
    }

    _collide() {
      const env = this.env, p = this.p;
      if (!env.contacts) return;
      this.hitPoints.forEach((hp, idx) => {
        if (idx < 4 && this.damage.arm[idx]) return; // abgebrochener Arm trifft nichts mehr
        const r = qRot(this.q, hp.b);
        const wp = add(this.pos, r);
        const hits = env.contacts(wp, hp.r);
        for (const hit of hits) this._impulse(r, hit, idx);
      });
    }

    _impulse(r, hit, idx) {
      const p = this.p, n = hit.n, mat = hit.mat;
      const ww = qRot(this.q, this.wb);
      const vp = add(this.vel, cross(ww, r));
      const vn = dot(vp, n);
      // positional correction
      this.pos = add(this.pos, scl(n, hit.depth * 0.9));
      if (vn >= 0) return;
      const invIw = (v) => { const b = qRotInv(this.q, v); return qRot(this.q, [b[0] * this.invI[0], b[1] * this.invI[1], b[2] * this.invI[2]]); };
      const rn = cross(r, n);
      const kN = 1 / p.mass + dot(n, cross(invIw(rn), r));
      const e = -vn < 0.6 ? 0 : mat.e;
      const j = -(1 + e) * vn / kN;
      let J = scl(n, j);
      // friction
      const vt = sub(vp, scl(n, vn)), vtl = len(vt);
      if (vtl > 1e-5) {
        const t = scl(vt, 1 / vtl), rt = cross(r, t);
        const kT = 1 / p.mass + dot(t, cross(invIw(rt), r));
        const jt = Math.max(-vtl / kT, -mat.mu * j);
        J = add(J, scl(t, jt));
      }
      this.vel = add(this.vel, scl(J, 1 / p.mass));
      const dW = invIw(cross(r, J));
      const nw = add(ww, dW);
      this.wb = qRotInv(this.q, nw);
      if (-vn > 2) this.events.push({ type: 'hit', speed: -vn, mat: mat.name });
      this._damage(idx, -vn, vtl, mat, n);
      if (this.cfg.crashDamage && mat.hard && -vn > Math.min(mat.crash, p.crashSpeed) && this.armed && !this.crashed) {
        this.crashed = true; this.armed = false;
        this.events.push({ type: 'crash', speed: -vn, mat: mat.name });
      }
    }

    // ---------- Schaden ----------
    repair() {
      this.damage = { prop: [0, 0, 0, 0], motor: [0, 0, 0, 0], arm: [false, false, false, false], cam: 0, frame: 0, battery: 0, power: true };
      for (const m of this.motors || []) m.imb = 0;
    }

    /** kleine Copter sind robuster (Whoop), grosse empfindlicher; ein angeschlagener Rahmen gibt schneller nach */
    _fragility() { return (11 / this.p.crashSpeed) * (1 + 0.5 * this.damage.frame); }

    _note(part, level, text) { this.events.push({ type: 'damage', part, level, text }); }

    _hurtProp(i, amount, cause) {
      const d = this.damage, before = d.prop[i];
      d.prop[i] = clamp(before + amount, 0, 1);
      for (const [lim, text] of [[0.25, 'angeschlagen'], [0.6, 'stark beschädigt'], [0.97, 'abgebrochen']]) {
        if (before < lim && d.prop[i] >= lim) this._note(`prop${i}`, lim, `Propeller M${i + 1} ${text}` + (cause ? ` (${cause})` : ''));
      }
    }

    _hurtCam(amount) {
      const d = this.damage, before = d.cam;
      d.cam = clamp(before + amount, 0, 1);
      if (before < 0.3 && d.cam >= 0.3) this._note('cam', 0.3, 'Kamera verbogen');
      if (before < 0.85 && d.cam >= 0.85) this._note('cam', 0.85, 'Kamera ausgefallen');
    }

    /** Aufprall am Trefferpunkt idx (0–3 Motoren, 4 oben/Akku, 5 unten, 6 vorne/Kamera, 7 hinten) */
    _damage(idx, vn, vt, mat, n) {
      if (!this.cfg.damage || idx === undefined) return;
      const d = this.damage, hard = mat.dmg ?? 1, frag = this._fragility();
      const s = vn * hard;                         // wirksame Aufprallgeschwindigkeit (m/s)
      // Richtung des Treffers im Copter: entlang der Motorachse (Landen, Aufsetzen) oder seitlich/frontal
      const nb = n ? qRotInv(this.q, n) : [0, 0, 1];
      const axial = Math.abs(nb[1]) > 0.7;
      if (idx < 4) {
        const m = this.motors[idx];
        // seitlich: drehende Propeller splittern schon beim Streifen; von unten trifft erst der Motor,
        // der Propeller bricht dort nur bei hartem Aufschlag
        const hitV = Math.hypot(vn, vt * 0.5) * hard;
        if (!axial && m.w > 300 && hitV > 0.8) this._hurtProp(idx, (0.1 + 0.065 * hitV) * frag * (0.6 + 0.4 * Math.min(1, m.w / 2500)), mat.name);
        else if (s > 3) this._hurtProp(idx, 0.04 * (s - 2) * frag, mat.name);
        // harter Frontaltreffer auf einen vorderen Motor biegt die Kamerahalterung mit
        if (m.r[2] < 0 && nb[2] > 0.6 && s > 6) this._hurtCam((s - 6) * 0.05 * frag);
        if (s > 6) {
          const before = d.motor[idx];
          d.motor[idx] = clamp(before + (s - 6) * 0.05 * frag, 0, 1);
          if (before < 0.3 && d.motor[idx] >= 0.3) this._note(`motor${idx}`, 0.3, `Motor M${idx + 1} verbogen`);
          if (before < 1 && d.motor[idx] >= 1) this._note(`motor${idx}`, 1, `Motor M${idx + 1} defekt`);
        }
        const armBreak = this.p.crashSpeed * 1.25 * (1 - 0.35 * d.frame);
        if (!d.arm[idx] && s > armBreak) {
          d.arm[idx] = true; d.prop[idx] = 1;
          this._note(`arm${idx}`, 1, `Arm M${idx + 1} gebrochen`);
        }
      } else if (idx === 6 && s > 2) {
        this._hurtCam((s - 2) * 0.06 * frag);
      } else if ((idx === 4 || idx === 5) && s > 5) {
        const before = d.battery;
        d.battery = clamp(before + (s - 5) * 0.04 * frag, 0, 1);
        if (before < 0.3 && d.battery >= 0.3) this._note('battery', 0.3, 'Akku beschädigt');
        if (d.battery >= 1 && d.power) { d.power = false; this._note('battery', 1, 'Akkukabel abgerissen'); }
      }
      if (s > 4) {
        const before = d.frame;
        d.frame = clamp(before + (s - 4) * 0.02 * frag, 0, 1);
        if (before < 0.5 && d.frame >= 0.5) this._note('frame', 0.5, 'Rahmen angeknackst');
      }
    }

    /** Zustand 0 (heil) bis 1 (kaputt) für Anzeigen */
    damageSummary() {
      const d = this.damage;
      return {
        motors: [0, 1, 2, 3].map((i) => ({ prop: d.prop[i], motor: d.motor[i], arm: d.arm[i] })),
        cam: d.cam, frame: d.frame, battery: d.battery, power: d.power,
        any: d.prop.some((v) => v > 0.02) || d.motor.some((v) => v > 0.02) || d.arm.some(Boolean) || d.cam > 0.02 || d.frame > 0.05 || d.battery > 0.02 || !d.power,
      };
    }
  }

  G.FPVCore = { Quad, PRESETS, derivePreset, applyRates, qRot, qRotInv, qMul, qAxis, qNorm, cross, dot, add, sub, scl, len, clamp, D2R, R2D, gauss, ocv };
})(typeof window !== 'undefined' ? window : globalThis);
