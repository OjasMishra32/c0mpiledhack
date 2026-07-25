import { useEffect, useRef, useState } from 'react';
import { ToolbarButton } from './primitives';

/**
 * Ask the live camera a question.
 *
 * This exists for one moment: a judge asks "is this actually real?" and you hand them the
 * keyboard. Nothing else in the product answers that question as completely, because the
 * answer comes from the current frames and nowhere else.
 */

interface AskFeedProps {
  answer: { question: string; answer: string } | null;
  available: boolean;
  onAsk: (question: string) => void;
  onDismiss: () => void;
}

export function AskFeed({ answer, available, onAsk, onDismiss }: AskFeedProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (answer) setPending(false);
  }, [answer]);

  // Answers clear themselves — one less thing to click during a demo.
  useEffect(() => {
    if (!answer) return;
    const t = window.setTimeout(onDismiss, 12000);
    return () => window.clearTimeout(t);
  }, [answer, onDismiss]);

  if (!open) {
    return <ToolbarButton onClick={() => setOpen(true)}>Ask the feed</ToolbarButton>;
  }

  return (
    <div className="relative flex items-center gap-2">
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false);
          if (e.key === 'Enter' && draft.trim()) {
            onAsk(draft.trim());
            setPending(true);
            setDraft('');
          }
        }}
        placeholder={available ? 'Ask about what the camera sees…' : 'Scene reasoning unavailable'}
        aria-label="Ask the camera feed a question"
        disabled={!available}
        spellCheck={false}
        className="h-9 w-72 rounded-control bg-surface-secondary px-3 text-[14px] leading-none text-text-primary placeholder:text-text-tertiary disabled:opacity-40"
      />
      <ToolbarButton onClick={() => setOpen(false)}>Close</ToolbarButton>

      {(pending || answer) && (
        <div
          role="status"
          aria-live="polite"
          className="absolute right-0 top-11 z-40 w-[420px] rounded-surface border border-separator-strong bg-surface-elevated p-4 shadow-2xl"
        >
          {pending && !answer ? (
            <span className="text-[14px] text-text-tertiary">Looking…</span>
          ) : (
            <>
              <p className="text-[11px] font-semibold uppercase leading-none tracking-[0.14em] text-text-tertiary">
                Asked
              </p>
              <p className="mt-2 text-[14px] leading-snug text-text-secondary">{answer!.question}</p>
              <p className="mt-3 text-[16px] leading-snug text-text-primary">{answer!.answer}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
