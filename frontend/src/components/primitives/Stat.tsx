interface StatProps {
  value: string | number;
  label: string;
  size?: 'hero' | 'panel';
  className?: string;
}

export function Stat({ value, label, size = 'panel', className = '' }: StatProps) {
  const valueClass = size === 'hero' ? 'text-[32px] font-semibold' : 'text-[26px] font-semibold';

  return (
    <div className={`flex flex-col items-start gap-0.5 ${className}`}>
      <span className={`tabular-nums text-text-primary ${valueClass}`}>{value}</span>
      <span className="text-[12px] text-text-tertiary">{label}</span>
    </div>
  );
}
