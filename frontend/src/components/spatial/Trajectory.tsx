import { useFrame } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { Homography } from '../../lib/homography';
import type { ObservedObject } from '../../types/hive';
import { useTableProjection } from './projection';

const MAX_POINTS = 15;
const SAMPLE_MS = 110;
const MIN_STEP_UV = 0.004; // ignore tracker jitter; only real movement leaves a trail

/**
 * A short fading trail behind each object.
 *
 * Costs almost nothing and does a lot: a still frame of this view looks like a
 * diagram, but a trailing object looks like something being *watched*. The fade
 * runs the vertex colours down toward the background rather than using per-vertex
 * alpha, which `LineBasicMaterial` does not support.
 */
export function Trajectory({
  object,
  homography,
}: {
  object: ObservedObject;
  homography: Homography;
}) {
  const project = useTableProjection(homography);
  const history = useRef<[number, number][]>([]);
  const lastSample = useRef(0);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3));
    geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3));
    geo.setDrawRange(0, 0);
    return geo;
  }, []);

  const material = useMemo(
    () => new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9 }),
    [],
  );
  const line = useMemo(() => new THREE.Line(geometry, material), [geometry, material]);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  const base = useMemo(
    () => new THREE.Color(object.descriptor.color_hex),
    [object.descriptor.color_hex],
  );

  useFrame(() => {
    const now = performance.now();
    const trail = history.current;

    if (now - lastSample.current > SAMPLE_MS) {
      lastSample.current = now;
      const next: [number, number] = [object.position.x, object.position.y];
      const prev = trail[trail.length - 1];
      if (!prev || Math.hypot(prev[0] - next[0], prev[1] - next[1]) > MIN_STEP_UV) {
        trail.push(next);
        if (trail.length > MAX_POINTS) trail.shift();
      }
    }

    if (trail.length < 2) {
      geometry.setDrawRange(0, 0);
      return;
    }

    const pos = geometry.attributes.position as THREE.BufferAttribute;
    const col = geometry.attributes.color as THREE.BufferAttribute;
    for (let i = 0; i < trail.length; i++) {
      const [x, y] = project(trail[i][0], trail[i][1]);
      pos.setXYZ(i, x, y, 0.001);
      const t = i / (trail.length - 1); // 0 = oldest, 1 = newest
      const fade = t * t; // bias the fade so only the recent tail reads strongly
      col.setXYZ(i, base.r * fade, base.g * fade, base.b * fade);
    }
    pos.needsUpdate = true;
    col.needsUpdate = true;
    geometry.setDrawRange(0, trail.length);
  });

  if (!object.visible) return null;
  return <primitive object={line} />;
}
