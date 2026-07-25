import type { ReactNode } from 'react';

interface SectionHeadingProps {
  children: ReactNode;
  right?: ReactNode;
  className?: string;
}

export function SectionHeading({ children, right, className = '' }: SectionHeadingProps) {
  return (
    <div className={`flex items-center justify-between ${className}`}>
      <h2 className="text-[15px] font-semibold text-text-primary">{children}</h2>
      {right}
    </div>
  );
}
