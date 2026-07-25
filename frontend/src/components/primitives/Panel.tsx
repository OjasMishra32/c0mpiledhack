import type { ReactNode } from 'react';

interface PanelProps {
  label?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  scroll?: boolean;
}

export function Panel({ label, right, children, className = '', scroll = false }: PanelProps) {
  return (
    <div className={`flex h-full flex-col bg-surface-primary ${className}`}>
      {label && (
        <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-separator px-4">
          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-tertiary">
            {label}
          </span>
          {right}
        </div>
      )}
      <div className={scroll ? 'min-h-0 flex-1 overflow-y-auto' : 'min-h-0 flex-1'}>{children}</div>
    </div>
  );
}
