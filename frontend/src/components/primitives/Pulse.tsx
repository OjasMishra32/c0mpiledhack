interface PulseProps {
  color?: string;
  size?: number;
  active?: boolean;
  className?: string;
}

/** A dot that breathes when `active`, static otherwise. Motion only on real state change. */
export function Pulse({ color = 'var(--ok)', size = 8, active = true, className = '' }: PulseProps) {
  return (
    <span className={`relative inline-flex ${className}`} style={{ width: size, height: size }}>
      {active && (
        <span
          className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-40"
          style={{ backgroundColor: color, animationDuration: '1.6s' }}
        />
      )}
      <span
        className="relative inline-flex h-full w-full rounded-full"
        style={{ backgroundColor: color }}
      />
    </span>
  );
}
