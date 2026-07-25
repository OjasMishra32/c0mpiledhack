import type { ReactNode } from 'react';

interface SectionHeadingProps {
  children: ReactNode;
  right?: ReactNode;
  className?: string;
}

/**
 * The title of a panel section — often an entity name (a worker, an action), so it
 * stays sentence-cased and readable rather than shrinking into a tiny label.
 */
export function SectionHeading({ children, right, className = '' }: SectionHeadingProps) {
  return (
    <div className={`flex items-start justify-between gap-3 ${className}`}>
      <h2 className="text-[15px] font-semibold leading-snug tracking-tight text-text-primary">
        {children}
      </h2>
      {right && <span className="mt-1 shrink-0">{right}</span>}
    </div>
  );
}

/**
 * The other half of the hierarchy: a category marker, never a name. 11px is the
 * floor for anything that carries meaning, and uppercase plus wide tracking is what
 * keeps it legible at that size on a projector.
 */
export function SectionLabel({ children, right, className = '' }: SectionHeadingProps) {
  return (
    <div className={`flex h-4 items-center justify-between gap-3 ${className}`}>
      <h2 className="text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-text-tertiary">
        {children}
      </h2>
      {right}
    </div>
  );
}
