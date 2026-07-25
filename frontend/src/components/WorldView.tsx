import { Canvas } from '@react-three/fiber';
import { useEffect, useMemo, useState } from 'react';
import { useCameraStream } from '../hooks/useCameraStream';
import {
  computeHomography,
  IDENTITY_TABLE_CORNERS,
  type Homography,
  type Point,
} from '../lib/homography';
import type { ObservedObject, Zone } from '../types/hive';
import { CalibrationOverlay } from './spatial/CalibrationOverlay';
import { Fallback2D } from './spatial/Fallback2D';
import { ObjectAnchor } from './spatial/ObjectAnchor';
import { PathLine } from './spatial/PathLine';
import { TargetZone } from './spatial/TargetZone';
import { VideoBackground } from './spatial/VideoBackground';
import { SecondaryButton } from './primitives/Button';

function detectWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

export interface ActivePath {
  id: string;
  from: [number, number];
  to: [number, number];
  color?: string;
}

interface WorldViewProps {
  objects: ObservedObject[];
  zones: Zone[];
  activePaths?: ActivePath[];
  selectedObjectId?: string | null;
  onSelectObject?: (id: string | null) => void;
  selectedZoneId?: string | null;
  onSelectZone?: (id: string | null) => void;
}

export function WorldView({
  objects,
  zones,
  activePaths = [],
  selectedObjectId = null,
  onSelectObject,
  selectedZoneId = null,
  onSelectZone,
}: WorldViewProps) {
  const { videoRef, status } = useCameraStream();
  const [webglAvailable] = useState(detectWebGL);
  const [calibrating, setCalibrating] = useState(false);
  const [calibrationPoints, setCalibrationPoints] = useState<Point[]>([]);
  const [homography, setHomography] = useState<Homography>(() =>
    computeHomography(IDENTITY_TABLE_CORNERS, IDENTITY_TABLE_CORNERS),
  );
  const [videoReady, setVideoReady] = useState(false);
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden');

  useEffect(() => {
    const onVisibility = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  useEffect(() => {
    const stored = sessionStorage.getItem('hive_calibration');
    if (stored) {
      try {
        const pts: Point[] = JSON.parse(stored);
        setHomography(computeHomography(IDENTITY_TABLE_CORNERS, pts));
      } catch {
        /* ignore malformed session data */
      }
    }
  }, []);

  useEffect(() => {
    if (status !== 'ready') return;
    const v = videoRef.current;
    if (!v) return;
    const onPlaying = () => setVideoReady(true);
    v.addEventListener('playing', onPlaying);
    return () => v.removeEventListener('playing', onPlaying);
  }, [status, videoRef]);

  const showVideo = status === 'ready' && videoReady;

  function finishCalibration(points: Point[]) {
    if (points.length === 4) {
      setHomography(computeHomography(IDENTITY_TABLE_CORNERS, points));
      sessionStorage.setItem('hive_calibration', JSON.stringify(points));
    }
    setCalibrating(false);
    setCalibrationPoints([]);
  }

  const objectPositionById = useMemo(
    () => new Map(objects.map((o) => [o.id, [o.position.x, o.position.y] as [number, number]])),
    [objects],
  );

  return (
    <div className="relative h-full w-full overflow-hidden bg-background">
      <video ref={videoRef} muted playsInline className="hidden" />

      {!webglAvailable ? (
        <Fallback2D
          objects={objects}
          zones={zones}
          homography={homography}
          selectedId={selectedObjectId}
          onSelectObject={(id) => onSelectObject?.(id)}
        />
      ) : (
        <Canvas
          camera={{ position: [0, 0, 5], fov: 50 }}
          gl={{ antialias: true, alpha: false }}
          onPointerMissed={() => {
            onSelectObject?.(null);
            onSelectZone?.(null);
          }}
          frameloop={pageVisible ? 'always' : 'never'}
        >
          <color attach="background" args={['#0b0b0c']} />
          {showVideo && videoRef.current && <VideoBackground video={videoRef.current} />}

          {zones.map((z) => (
            <TargetZone
              key={z.id}
              zone={z}
              homography={homography}
              active={z.status === 'active' || z.id === selectedZoneId}
              selected={z.id === selectedZoneId}
              onSelect={() => onSelectZone?.(z.id)}
            />
          ))}

          {objects.map((o) => (
            <ObjectAnchor
              key={o.id}
              object={o}
              homography={homography}
              selected={o.id === selectedObjectId}
              onSelect={() => onSelectObject?.(o.id)}
            />
          ))}

          {activePaths.map((p) => {
            const from = objectPositionById.get(p.id) ?? p.from;
            return <PathLine key={p.id} from={from} to={p.to} homography={homography} color={p.color} />;
          })}
        </Canvas>
      )}

      {calibrating && (
        <CalibrationOverlay
          points={calibrationPoints}
          onAddPoint={(pt) => {
            const next = [...calibrationPoints, pt];
            setCalibrationPoints(next);
            if (next.length === 4) finishCalibration(next);
          }}
          onReset={() => setCalibrationPoints([])}
          onCancel={() => finishCalibration(calibrationPoints)}
        />
      )}

      {status === 'denied' || status === 'unavailable' ? (
        <div className="pointer-events-none absolute left-4 top-4 text-[12px] text-text-tertiary">
          Simulation — camera unavailable
        </div>
      ) : null}

      {!calibrating && (
        <div className="absolute bottom-4 right-4">
          <SecondaryButton onClick={() => setCalibrating(true)} className="bg-surface-elevated/90">
            Calibrate workspace
          </SecondaryButton>
        </div>
      )}
    </div>
  );
}
