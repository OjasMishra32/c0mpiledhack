// Planar homography: maps canonical table-space coordinates (0..1, 0..1) onto the
// quadrilateral a host traces on the live camera frame during calibration. This is what
// lets zone rectangles and object anchors — which arrive as normalized table coordinates
// per docs/CONTRACTS.md — draw as a correctly skewed quad on a camera that isn't overhead.

export type Point = [number, number];
export type Homography = number[]; // row-major 3x3, h[8] normalized to 1

function solveLinearSystem(A: number[][], b: number[]): number[] {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);

  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let row = col + 1; row < n; row++) {
      if (Math.abs(M[row][col]) > Math.abs(M[pivot][col])) pivot = row;
    }
    [M[col], M[pivot]] = [M[pivot], M[col]];

    const pivotVal = M[col][col];
    if (Math.abs(pivotVal) < 1e-12) continue;
    for (let row = 0; row < n; row++) {
      if (row === col) continue;
      const factor = M[row][col] / pivotVal;
      for (let k = col; k <= n; k++) M[row][k] -= factor * M[col][k];
    }
  }

  return M.map((row, i) => row[n] / (row[i] || 1e-12));
}

/** Computes H such that applyHomography(H, ...src) ≈ dst, for exactly 4 correspondences. */
export function computeHomography(src: Point[], dst: Point[]): Homography {
  if (src.length !== 4 || dst.length !== 4) {
    throw new Error('computeHomography requires exactly 4 point correspondences');
  }

  const A: number[][] = [];
  const b: number[] = [];

  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i];
    const [xp, yp] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -x * xp, -y * xp]);
    b.push(xp);
    A.push([0, 0, 0, x, y, 1, -x * yp, -y * yp]);
    b.push(yp);
  }

  const h = solveLinearSystem(A, b);
  return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1];
}

export function applyHomography(h: Homography, x: number, y: number): Point {
  const w = h[6] * x + h[7] * y + h[8];
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}

export const IDENTITY_TABLE_CORNERS: Point[] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
];
