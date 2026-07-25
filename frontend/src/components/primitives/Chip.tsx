import type { ReactNode } from 'react';

type ChipTone = 'default' | 'ok' | 'warn' | 'crit' | 'info' | 'think';

const TONE_CLASS: Record<ChipTone, string> = {
  default: 'text-fg-2 border-line-strong',
  ok: 'text-ok border-ok/40',
  warn: 'text-warn border-warn/40',
  crit: 'text-crit border-crit/40',
  info: 'text-info border-info/40',
  think: 'text-think border-think/40',
};

interface ChipProps {
  children: ReactNode;
  tone?: ChipTone;
  className?: string;
}

export function Chip({ children, tone = 'default', className = '' }: ChipProps) {
  return (
    <span
      className={`inline-flex items-center rounded-sm border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.1em] ${TONE_CLASS[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
