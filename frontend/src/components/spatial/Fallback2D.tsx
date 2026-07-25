import type { ObservedObject, Zone } from '../../types/hive';
import { applyHomography, type Homography } from '../../lib/homography';

interface Fallback2DProps {
  objects: ObservedObject[];
  zones: Zone[];
  homography: Homography;
  selectedId: string | null;
  onSelectObject: (id: string) => void;
}

/** SVG stand-in used when WebGL is unavailable. Same math, same data, degraded rendering only. */
export function Fallback2D({ objects, zones, homography, selectedId, onSelectObject }: Fallback2DProps) {
  return (
    <div className="absolute inset-0">
      <svg className="h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        {zones.map((z) => {
          const corners = [
            applyHomography(homography, z.bounds.x, z.bounds.y),
            applyHomography(homography, z.bounds.x + z.bounds.w, z.bounds.y),
            applyHomography(homography, z.bounds.x + z.bounds.w, z.bounds.y + z.bounds.h),
            applyHomography(homography, z.bounds.x, z.bounds.y + z.bounds.h),
          ];
          const pts = corners.map(([x, y]) => `${x * 100},${y * 100}`).join(' ');
          return <polygon key={z.id} points={pts} fill="rgba(255,255,255,0.04)" stroke="rgba(255,255,255,0.3)" strokeWidth={0.15} />;
        })}
        {objects.map((o) => {
          const [x, y] = applyHomography(homography, o.position.x, o.position.y);
          const selected = o.id === selectedId;
          return (
            <circle
              key={o.id}
              cx={x * 100} cy={y * 100} r={selected ? 2 : 1.5}
              fill="none" stroke={selected ? '#f2c94c' : o.descriptor.color_hex} strokeWidth={0.4}
              className="cursor-pointer"
              onClick={() => onSelectObject(o.id)}
            />
          );
        })}
      </svg>
      <div className="pointer-events-none absolute left-4 top-4 text-[12px] text-text-tertiary">2D overlay mode</div>
    </div>
  );
}
