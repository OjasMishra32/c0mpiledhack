import { useEffect, useMemo } from 'react';
import * as THREE from 'three';

interface Line3DProps {
  geometry: THREE.BufferGeometry;
  color: string;
  opacity?: number;
}

/**
 * @react-three/fiber v8's types omit the `line` intrinsic (it collides with the DOM SVG
 * `<line>` tag, even though the reconciler handles it fine at runtime). Building the
 * THREE.Line ourselves sidesteps the gap without fighting the type declarations.
 */
export function Line3D({ geometry, color, opacity = 1 }: Line3DProps) {
  const material = useMemo(
    () => new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }),
    [color, opacity],
  );
  const line = useMemo(() => new THREE.Line(geometry, material), [geometry, material]);
  useEffect(() => () => material.dispose(), [material]);

  return <primitive object={line} />;
}
