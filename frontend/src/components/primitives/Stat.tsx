interface StatProps {
  value: string | number;
  label: string;
  size?: 'hero' | 'panel';
  className?: string;
}

export function Stat({ value, label, size = 'panel', className = '' }: StatProps) {
  const valueClass =
    size === 'hero'
      ? 'text-[64px] font-light leading-none tracking-[-0.03em]'
      : 'text-[34px] font-normal leading-none tracking-[-0.02em]';

  return (
    <div className={`flex flex-col items-start gap-1 ${className}`}>
      <span className={`font-mono tabular-nums text-fg-0 ${valueClass}`}>{value}</span>
      <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-2">
        {label}
      </span>
    </div>
  );
}
