import { Html } from '@react-three/drei';
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { Zone } from '../../types/hive';
import type { Homography } from '../../lib/homography';
import { Line3D } from './Line3D';
import { useTableProjection } from './projection';

interface TargetZoneProps {
  zone: Zone;
  homography: Homography;
  active: boolean;
  selected: boolean;
  onSelect: () => void;
}

const STATUS_COLOR: Record<Zone['status'], string> = {
  unknown: '#6e6e73',
  pending: '#a1a1a6',
  active: '#f2c94c',
  satisfied: '#30d158',
  blocked: '#ff453a',
};

export function TargetZone({ zone, homography, active, selected, onSelect }: TargetZoneProps) {
  const project = useTableProjection(homography);
  const { x, y, w, h } = zone.bounds;

  const corners = useMemo(
    () => [
      project(x, y),
      project(x + w, y),
      project(x + w, y + h),
      project(x, y + h),
    ],
    [project, x, y, w, h],
  );

  const fillGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array([
      corners[0][0], corners[0][1], 0,
      corners[1][0], corners[1][1], 0,
      corners[2][0], corners[2][1], 0,
      corners[0][0], corners[0][1], 0,
      corners[2][0], corners[2][1], 0,
      corners[3][0], corners[3][1], 0,
    ]);
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [corners]);

  const outlineGeometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const pts = [...corners, corners[0]];
    const positions = new Float32Array(pts.flatMap(([px, py]) => [px, py, 0.001]));
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [corners]);

  useEffect(() => () => {
    fillGeometry.dispose();
    outlineGeometry.dispose();
  }, [fillGeometry, outlineGeometry]);

  const color = selected ? '#f2c94c' : STATUS_COLOR[zone.status];
  const fillOpacity = active || zone.status === 'blocked' || selected ? 0.1 : 0.04;
  const labelAnchor = corners[0];

  return (
    <group onClick={(e) => { e.stopPropagation(); onSelect(); }}>
      <mesh geometry={fillGeometry} position={[0, 0, -0.005]}>
        <meshBasicMaterial color={color} transparent opacity={fillOpacity} side={THREE.DoubleSide} />
      </mesh>
      <Line3D geometry={outlineGeometry} color={color} opacity={active || selected ? 0.9 : 0.35} />
      <Html position={[labelAnchor[0], labelAnchor[1], 0]} style={{ pointerEvents: 'none', transform: 'translate(4px, 4px)' }}>
        <div className="whitespace-nowrap text-[11px] text-text-tertiary">{zone.label}</div>
      </Html>
    </group>
  );
}
