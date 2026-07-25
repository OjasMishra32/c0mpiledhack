import type { ReactNode } from 'react';

interface InspectorRowProps {
  label: string;
  value: ReactNode;
  className?: string;
}

export function InspectorRow({ label, value, className = '' }: InspectorRowProps) {
  return (
    <div className={`flex items-baseline justify-between gap-4 py-1.5 ${className}`}>
      <span className="shrink-0 text-[13px] leading-relaxed text-text-tertiary">{label}</span>
      <span className="text-right text-[13px] leading-relaxed text-text-primary">{value}</span>
    </div>
  );
}
