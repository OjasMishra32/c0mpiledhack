import type { ReactNode } from 'react';

interface InspectorRowProps {
  label: string;
  value: ReactNode;
  className?: string;
}

export function InspectorRow({ label, value, className = '' }: InspectorRowProps) {
  return (
    <div className={`flex items-start justify-between gap-4 py-2 ${className}`}>
      <span className="text-[13px] text-text-tertiary">{label}</span>
      <span className="text-right text-[13px] text-text-primary">{value}</span>
    </div>
  );
}
