interface StatProps {
  value: string | number;
  label: string;
  /** `hero` is the end-of-run screenshot scale; `panel` sits inside a sidebar. */
  size?: 'hero' | 'panel';
  align?: 'start' | 'center';
  className?: string;
}

/**
 * A number and what it means. The number is set light and tabular so a row of them
 * stays optically level and does not jitter while it counts up; the label is the
 * standard 11px uppercase marker.
 */
export function Stat({ value, label, size = 'panel', align = 'start', className = '' }: StatProps) {
  const hero = size === 'hero';
  const valueClass = hero
    ? 'text-[64px] font-light leading-[0.95] tracking-[-0.03em]'
    : 'text-[30px] font-light leading-none tracking-[-0.02em]';

  return (
    <div
      className={`flex min-w-0 flex-col ${hero ? 'gap-3' : 'gap-1.5'} ${align === 'center' ? 'items-center text-center' : 'items-start'} ${className}`}
    >
      <span className={`tabular-nums text-text-primary ${valueClass}`}>{value}</span>
      <span className="text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-text-tertiary">
        {label}
      </span>
    </div>
  );
}
