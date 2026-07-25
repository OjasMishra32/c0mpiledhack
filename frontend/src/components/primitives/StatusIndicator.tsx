export type StatusTone = 'neutral' | 'success' | 'warning' | 'failure' | 'information' | 'accent';

const TONE_COLOR: Record<StatusTone, string> = {
  neutral: 'var(--text-tertiary)',
  success: 'var(--success)',
  warning: 'var(--warning)',
  failure: 'var(--failure)',
  information: 'var(--information)',
  accent: 'var(--hive-accent)',
};

interface StatusIndicatorProps {
  tone?: StatusTone;
  color?: string;
  size?: number;
  outline?: boolean;
  className?: string;
}

/** A single static dot. No pulsing, no glow — color alone carries meaning. */
export function StatusIndicator({ tone = 'neutral', color, size = 7, outline = false, className = '' }: StatusIndicatorProps) {
  const resolved = color ?? TONE_COLOR[tone];
  return (
    <span
      className={`inline-block shrink-0 rounded-full ${outline ? 'border border-dashed' : ''} ${className}`}
      style={{
        width: size,
        height: size,
        backgroundColor: outline ? 'transparent' : resolved,
        borderColor: outline ? resolved : undefined,
      }}
    />
  );
}
