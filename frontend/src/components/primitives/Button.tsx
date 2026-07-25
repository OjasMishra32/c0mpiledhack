import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
}

/**
 * One control height (36px) and one type size for every clickable control in the
 * header band, so the toolbar and the objective bar read as a single row of
 * controls from across the room.
 */
const BASE =
  'inline-flex h-9 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-control ' +
  'text-[14px] font-medium tracking-tight transition-colors duration-150 ease-standard ' +
  'disabled:cursor-default disabled:opacity-35';

/** The filled accent control. Exactly one of these is on screen at a time. */
export function PrimaryButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`${BASE} bg-accent px-4 text-accent-ink hover:bg-[rgb(var(--hive-accent-rgb)/0.88)] ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function SecondaryButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`${BASE} border border-separator-strong bg-transparent px-4 text-text-primary hover:bg-surface-secondary ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function DangerButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`${BASE} border border-[rgb(var(--failure-rgb)/0.5)] bg-transparent px-4 text-failure hover:bg-failure hover:text-white ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function ToolbarButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`${BASE} px-3 text-text-secondary hover:bg-surface-secondary hover:text-text-primary ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
