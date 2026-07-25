import { Html } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { ObservedObject } from '../../types/hive';
import type { Homography } from '../../lib/homography';
import { Line3D } from './Line3D';
import { useTableProjection } from './projection';

const SEGMENTS = 40;
const RADIUS_UV = 0.035;
const SMOOTHING = 0.18;

function objectLabel(o: ObservedObject): string {
  return o.role ?? o.semantic_label ?? `${o.descriptor.color_name} ${o.descriptor.shape_hint} object`;
}

interface ObjectAnchorProps {
  object: ObservedObject;
  homography: Homography;
  selected: boolean;
  onSelect: () => void;
}

export function ObjectAnchor({ object, homography, selected, onSelect }: ObjectAnchorProps) {
  const project = useTableProjection(homography);
  const viewportWidth = useThree((s) => s.viewport.width);
  const smoothed = useRef<[number, number]>([object.position.x, object.position.y]);
  const groupRef = useRef<THREE.Group>(null);

  const ringGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array((SEGMENTS + 1) * 3), 3));
    return geo;
  }, []);

  const markerGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(2 * 3), 3));
    return geo;
  }, []);

  useEffect(() => () => {
    ringGeometry.dispose();
    markerGeometry.dispose();
  }, [ringGeometry, markerGeometry]);

  useFrame(() => {
    smoothed.current[0] += (object.position.x - smoothed.current[0]) * SMOOTHING;
    smoothed.current[1] += (object.position.y - smoothed.current[1]) * SMOOTHING;
    const [u, v] = smoothed.current;

    const ringPos = ringGeometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i <= SEGMENTS; i++) {
      const t = (i / SEGMENTS) * Math.PI * 2;
      const [x, y] = project(u + Math.cos(t) * RADIUS_UV, v + Math.sin(t) * RADIUS_UV);
      ringPos.setXYZ(i, x, y, 0);
    }
    ringPos.needsUpdate = true;

    const [bx, by] = project(u, v);
    const [tx, ty] = project(u, v - 0.045);
    const markerPos = markerGeometry.attributes.position as THREE.BufferAttribute;
    markerPos.setXYZ(0, bx, by, 0);
    markerPos.setXYZ(1, tx, ty, 0.01);
    markerPos.needsUpdate = true;

    groupRef.current?.position.set(bx, by, 0);
  });

  const color = object.descriptor.color_hex;
  const dotRadius = viewportWidth * 0.012;

  return (
    <group>
      <Line3D geometry={ringGeometry} color={selected ? '#f2c94c' : color} />
      <Line3D geometry={markerGeometry} color={color} opacity={0.6} />
      <group ref={groupRef}>
        <mesh onClick={(e) => { e.stopPropagation(); onSelect(); }}>
          <circleGeometry args={[dotRadius, 16]} />
          <meshBasicMaterial color={color} />
        </mesh>
        <mesh onClick={(e) => { e.stopPropagation(); onSelect(); }}>
          <circleGeometry args={[dotRadius * 3, 16]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
        <Html
          style={{ pointerEvents: 'none', transform: 'translate(10px, -10px)' }}
        >
          <div className="whitespace-nowrap rounded-control bg-surface-elevated/90 px-1.5 py-0.5 text-[11px] text-text-secondary">
            {objectLabel(object)}
            {selected && (
              <span className="ml-1 tabular-nums text-text-tertiary">{Math.round(object.confidence * 100)}%</span>
            )}
          </div>
        </Html>
      </group>
    </group>
  );
}
