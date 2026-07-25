import { useFrame } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { Homography } from '../../lib/homography';
import { Line3D } from './Line3D';
import { useTableProjection } from './projection';

const SEGMENTS = 24;
const DRAW_MS = 380;

interface PathLineProps {
  from: [number, number];
  to: [number, number];
  homography: Homography;
  color?: string;
  active?: boolean;
}

/** Draws once when mounted (a new dispatch/reassignment), then holds — never loops. */
export function PathLine({ from, to, homography, color = '#f2c94c', active = true }: PathLineProps) {
  const project = useTableProjection(homography);
  const start = useRef(performance.now());

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array((SEGMENTS + 1) * 3), 3));
    geo.setDrawRange(0, 0);
    return geo;
  }, []);

  useEffect(() => () => geometry.dispose(), [geometry]);
  useEffect(() => { start.current = performance.now(); }, [from[0], from[1], to[0], to[1]]);

  useFrame(() => {
    const pos = geometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i <= SEGMENTS; i++) {
      const t = i / SEGMENTS;
      const u = from[0] + (to[0] - from[0]) * t;
      const v = from[1] + (to[1] - from[1]) * t;
      const [x, y] = project(u, v);
      pos.setXYZ(i, x, y, 0.002);
    }
    pos.needsUpdate = true;

    const progress = Math.min(1, (performance.now() - start.current) / DRAW_MS);
    geometry.setDrawRange(0, Math.round(progress * SEGMENTS) + 1);
  });

  return <Line3D geometry={geometry} color={color} opacity={active ? 0.85 : 0.25} />;
}
