import { useThree } from '@react-three/fiber';
import { useEffect, useMemo, useState } from 'react';
import * as THREE from 'three';

interface VideoBackgroundProps {
  video: HTMLVideoElement;
}

/** The webcam feed as a textured plane filling the frustum, `object-fit: cover` style. */
export function VideoBackground({ video }: VideoBackgroundProps) {
  const viewport = useThree((s) => s.viewport);
  const [videoSize, setVideoSize] = useState({ w: 16, h: 9 });

  const texture = useMemo(() => {
    const t = new THREE.VideoTexture(video);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }, [video]);

  useEffect(() => {
    const update = () => {
      if (video.videoWidth && video.videoHeight) {
        setVideoSize({ w: video.videoWidth, h: video.videoHeight });
      }
    };
    video.addEventListener('loadedmetadata', update);
    update();
    return () => video.removeEventListener('loadedmetadata', update);
  }, [video]);

  useEffect(() => () => texture.dispose(), [texture]);

  const videoAspect = videoSize.w / videoSize.h;
  const containerAspect = viewport.width / viewport.height;

  if (videoAspect > containerAspect) {
    texture.repeat.set(containerAspect / videoAspect, 1);
    texture.offset.set((1 - texture.repeat.x) / 2, 0);
  } else {
    texture.repeat.set(1, videoAspect / containerAspect);
    texture.offset.set(0, (1 - texture.repeat.y) / 2);
  }

  return (
    <mesh position={[0, 0, -0.01]}>
      <planeGeometry args={[viewport.width, viewport.height]} />
      <meshBasicMaterial map={texture} toneMapped={false} />
    </mesh>
  );
}
