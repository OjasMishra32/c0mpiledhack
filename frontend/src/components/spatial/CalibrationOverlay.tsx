import { useRef } from 'react';
import type { Point } from '../../lib/homography';
import { SecondaryButton } from '../primitives/Button';

interface CalibrationOverlayProps {
  points: Point[];
  onAddPoint: (p: Point) => void;
  onReset: () => void;
  onCancel: () => void;
}

const STEP_LABEL = ['top-left', 'top-right', 'bottom-right', 'bottom-left'];

export function CalibrationOverlay({ points, onAddPoint, onReset, onCancel }: CalibrationOverlayProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  function handleClick(e: React.MouseEvent) {
    if (points.length >= 4) return;
    const rect = containerRef.current!.getBoundingClientRect();
    onAddPoint([(e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height]);
  }

  return (
    <div ref={containerRef} onClick={handleClick} className="absolute inset-0 z-20 cursor-crosshair">
      <svg className="pointer-events-none absolute inset-0 h-full w-full">
        {points.map(([x, y], i) => (
          <circle key={i} cx={`${x * 100}%`} cy={`${y * 100}%`} r={5} fill="#f2c94c" stroke="#0b0b0c" strokeWidth={1.5} />
        ))}
        {points.length > 1 &&
          points.map((p, i) => {
            if (i === 0) return null;
            const prev = points[i - 1];
            return (
              <line
                key={i}
                x1={`${prev[0] * 100}%`} y1={`${prev[1] * 100}%`}
                x2={`${p[0] * 100}%`} y2={`${p[1] * 100}%`}
                stroke="#f2c94c" strokeWidth={1} strokeDasharray="4 4"
              />
            );
          })}
      </svg>

      <div className="absolute left-1/2 top-6 flex -translate-x-1/2 flex-col items-center gap-2 rounded-surface bg-surface-elevated/95 px-4 py-3 text-center">
        <span className="text-[14px] text-text-primary">
          {points.length < 4
            ? `Click the ${STEP_LABEL[points.length]} corner of the workspace`
            : 'Workspace calibrated'}
        </span>
        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
          <SecondaryButton onClick={onReset}>Reset</SecondaryButton>
          <SecondaryButton onClick={onCancel}>Done</SecondaryButton>
        </div>
      </div>
    </div>
  );
}
