export function Rule({ className = '' }: { className?: string }) {
  return <div className={`h-px bg-line ${className}`} />;
}
