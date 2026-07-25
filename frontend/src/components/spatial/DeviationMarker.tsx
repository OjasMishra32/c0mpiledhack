import { Html } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { Homography } from '../../lib/homography';
import { useTableProjection } from './projection';

const RING_SEGMENTS = 32;
const RADIUS_UV = 0.04;
const LEADER_SEGMENTS = 2;

/**
 * Where the object was supposed to be, tied by a dashed leader to where it
 * actually is. One glance tells the whole story: the plan said there, the world
 * says here.
 *
 * Both points come from real state — the expected position is the centre of the
 * paused action's target zone, not a parsed string — so this never drifts from
 * what the backend actually detected.
 */
export function DeviationMarker({
  expected,
  actual,
  homography,
}: {
  expected: [number, number];
  actual: [number, number];
  homography: Homography;
}) {
  const project = useTableProjection(homography);
  const labelRef = useRef<THREE.Group>(null);

  const ringGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array((RING_SEGMENTS + 1) * 3), 3),
    );
    return geo;
  }, []);

  const leaderGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array((LEADER_SEGMENTS + 1) * 3), 3),
    );
    return geo;
  }, []);

  const ringMaterial = useMemo(
    () => new THREE.LineBasicMaterial({ color: '#ff453a', transparent: true, opacity: 0.9 }),
    [],
  );
  const leaderMaterial = useMemo(
    () =>
      new THREE.LineDashedMaterial({
        color: '#ff453a',
        transparent: true,
        opacity: 0.75,
        dashSize: 0.06,
        gapSize: 0.05,
      }),
    [],
  );

  const ring = useMemo(() => new THREE.Line(ringGeometry, ringMaterial), [ringGeometry, ringMaterial]);
  const leader = useMemo(
    () => new THREE.Line(leaderGeometry, leaderMaterial),
    [leaderGeometry, leaderMaterial],
  );

  useEffect(
    () => () => {
      ringGeometry.dispose();
      leaderGeometry.dispose();
      ringMaterial.dispose();
      leaderMaterial.dispose();
    },
    [ringGeometry, leaderGeometry, ringMaterial, leaderMaterial],
  );

  useFrame(() => {
    const ringPos = ringGeometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i <= RING_SEGMENTS; i++) {
      const t = (i / RING_SEGMENTS) * Math.PI * 2;
      const [x, y] = project(
        expected[0] + Math.cos(t) * RADIUS_UV,
        expected[1] + Math.sin(t) * RADIUS_UV,
      );
      ringPos.setXYZ(i, x, y, 0.003);
    }
    ringPos.needsUpdate = true;

    const leaderPos = leaderGeometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i <= LEADER_SEGMENTS; i++) {
      const t = i / LEADER_SEGMENTS;
      const [x, y] = project(
        expected[0] + (actual[0] - expected[0]) * t,
        expected[1] + (actual[1] - expected[1]) * t,
      );
      leaderPos.setXYZ(i, x, y, 0.003);
    }
    leaderPos.needsUpdate = true;
    // Dash spacing is computed from world-space vertex distances, so it has to be
    // recomputed whenever the endpoints move.
    leader.computeLineDistances();

    const [lx, ly] = project(expected[0], expected[1] - RADIUS_UV);
    labelRef.current?.position.set(lx, ly, 0.004);
  });

  return (
    <group>
      <primitive object={ring} />
      <primitive object={leader} />
      <group ref={labelRef}>
        <Html style={{ pointerEvents: 'none', transform: 'translate(-50%, -22px)' }}>
          <div
            className="whitespace-nowrap rounded-control px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em]"
            style={{ background: 'rgba(11,11,12,0.85)', color: 'var(--failure)' }}
          >
            Expected
          </div>
        </Html>
      </group>
    </group>
  );
}
