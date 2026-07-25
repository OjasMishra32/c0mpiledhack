import type { ReactNode } from 'react';

interface EmptyStateProps {
  title: string;
  detail?: string;
  className?: string;
}

export function EmptyState({ title, detail, className = '' }: EmptyStateProps) {
  return (
    <div className={`flex h-full flex-col items-center justify-center gap-1 px-6 text-center ${className}`}>
      <span className="text-[14px] text-text-secondary">{title}</span>
      {detail && <span className="text-[13px] text-text-tertiary">{detail}</span>}
    </div>
  );
}

export function ErrorState({ title, detail, action, className = '' }: EmptyStateProps & { action?: ReactNode }) {
  return (
    <div className={`flex h-full flex-col items-center justify-center gap-2 px-6 text-center ${className}`}>
      <span className="text-[14px] text-failure">{title}</span>
      {detail && <span className="text-[13px] text-text-tertiary">{detail}</span>}
      {action}
    </div>
  );
}
