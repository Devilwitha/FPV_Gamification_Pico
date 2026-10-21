// Attitude estimation: gyro integration with a Mahony-style accelerometer correction.
// Body frame (Betaflight): x forward, y left, z up; gyro in deg/s, acc in g.
import type { Vec3Arr } from '../types.ts';

const DEG = Math.PI / 180;

export function computeOrientation(t: Float64Array, gyro: Vec3Arr, acc: Vec3Arr | null, kp = 0.6): Float32Array {
  const n = t.length;
  const out = new Float32Array(n * 4);
  let q0 = 1, q1 = 0, q2 = 0, q3 = 0;
  for (let i = 0; i < n; i++) {
    const dt = i ? Math.min(0.05, Math.max(0, t[i] - t[i - 1])) : 0;
    let gx = gyro[0][i] * DEG;
    let gy = gyro[1][i] * DEG;
    let gz = gyro[2][i] * DEG;
    if (acc) {
      let ax = acc[0][i], ay = acc[1][i], az = acc[2][i];
      const norm = Math.hypot(ax, ay, az);
      // only trust the accelerometer when it roughly measures gravity
      if (norm > 0.75 && norm < 1.25) {
        ax /= norm; ay /= norm; az /= norm;
        const vx = 2 * (q1 * q3 - q0 * q2);
        const vy = 2 * (q0 * q1 + q2 * q3);
        const vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;
        const gain = t[i] - t[0] < 1.5 ? 8 : kp; // converge quickly at start
        gx += gain * (ay * vz - az * vy);
        gy += gain * (az * vx - ax * vz);
        gz += gain * (ax * vy - ay * vx);
      }
    }
    const h = 0.5 * dt;
    const a = q0, b = q1, c = q2;
    q0 += (-b * gx - c * gy - q3 * gz) * h;
    q1 += (a * gx + c * gz - q3 * gy) * h;
    q2 += (a * gy - b * gz + q3 * gx) * h;
    q3 += (a * gz + b * gy - c * gx) * h;
    const r = 1 / Math.hypot(q0, q1, q2, q3);
    q0 *= r; q1 *= r; q2 *= r; q3 *= r;
    out[i * 4] = q0;
    out[i * 4 + 1] = q1;
    out[i * 4 + 2] = q2;
    out[i * 4 + 3] = q3;
  }
  return out;
}

/** Rotation matrix (row-major, body→world) from quaternion at sample i. */
export function rotationAt(quat: Float32Array, i: number, m: Float64Array = new Float64Array(9)): Float64Array {
  const w = quat[i * 4], x = quat[i * 4 + 1], y = quat[i * 4 + 2], z = quat[i * 4 + 3];
  m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y - w * z); m[2] = 2 * (x * z + w * y);
  m[3] = 2 * (x * y + w * z); m[4] = 1 - 2 * (x * x + z * z); m[5] = 2 * (y * z - w * x);
  m[6] = 2 * (x * z - w * y); m[7] = 2 * (y * z + w * x); m[8] = 1 - 2 * (x * x + y * y);
  return m;
}

/** Bank (roll) and pitch in degrees (nose up positive, right wing down positive). */
export function attitudeAt(quat: Float32Array, i: number): { roll: number; pitch: number } {
  const w = quat[i * 4], x = quat[i * 4 + 1], y = quat[i * 4 + 2], z = quat[i * 4 + 3];
  // world-up expressed in body frame
  const ux = 2 * (x * z - w * y);
  const uy = 2 * (w * x + y * z);
  const uz = w * w - x * x - y * y + z * z;
  return {
    roll: Math.atan2(uy, uz) / DEG,
    pitch: Math.asin(Math.max(-1, Math.min(1, ux))) / DEG,
  };
}
