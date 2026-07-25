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
        <div className="flex items-center justify-between px-4 pb-2 pt-3.5">
          <span className="text-[15px] font-semibold text-text-primary">{label}</span>
          {right}
        </div>
      )}
      <div className={scroll ? 'min-h-0 flex-1 overflow-y-auto' : 'min-h-0 flex-1'}>{children}</div>
    </div>
  );
}
