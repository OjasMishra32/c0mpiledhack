import { useThree } from '@react-three/fiber';
import { useCallback } from 'react';
import { applyHomography, type Homography } from '../../lib/homography';

/**
 * Maps canonical table coordinates (u,v in 0..1, per docs/CONTRACTS.md's normalized
 * positions) through the calibration homography into Three.js world units on the video
 * plane. Every spatial mesh in the overlay goes through this so a single calibration
 * keeps zones, anchors, and paths all attached to the same physical surface.
 */
export function useTableProjection(homography: Homography) {
  const viewport = useThree((s) => s.viewport);

  return useCallback(
    (u: number, v: number): [number, number] => {
      const [sx, sy] = applyHomography(homography, u, v);
      return [(sx - 0.5) * viewport.width, (0.5 - sy) * viewport.height];
    },
    [homography, viewport.width, viewport.height],
  );
}
