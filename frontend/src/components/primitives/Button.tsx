import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
}

export function PrimaryButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`rounded-control bg-accent px-4 py-1.5 text-[14px] font-medium text-accent-ink transition-opacity duration-150 ease-standard hover:opacity-90 disabled:opacity-40 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function SecondaryButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`rounded-control border border-separator-strong bg-transparent px-4 py-1.5 text-[14px] font-medium text-text-primary transition-colors duration-150 ease-standard hover:bg-surface-secondary disabled:opacity-40 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function DangerButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`rounded-control border border-failure/50 bg-transparent px-4 py-1.5 text-[14px] font-medium text-failure transition-colors duration-150 ease-standard hover:bg-failure hover:text-white disabled:opacity-40 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function ToolbarButton({ children, className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`rounded-control px-2.5 py-1 text-[13px] font-medium text-text-secondary transition-colors duration-150 ease-standard hover:bg-surface-secondary hover:text-text-primary disabled:opacity-40 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
