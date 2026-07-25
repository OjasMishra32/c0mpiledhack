import { useEffect, useRef, useState } from 'react';

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
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded-[--r-control] px-2.5 py-1.5 text-[13px] text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
      >
        Ask the feed
      </button>
    );
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
        disabled={!available}
        spellCheck={false}
        className="w-64 rounded-[--r-control] bg-surface-secondary px-2.5 py-1.5 text-[13px] text-text-primary outline-none placeholder:text-text-tertiary disabled:opacity-40"
      />
      <button
        onClick={() => setOpen(false)}
        className="text-[12px] text-text-tertiary hover:text-text-secondary"
      >
        Close
      </button>

      {(pending || answer) && (
        <div className="absolute right-0 top-9 z-40 w-96 rounded-[--r-surface] border border-separator-strong bg-surface-elevated p-3 shadow-2xl">
          {pending && !answer ? (
            <span className="text-[13px] text-text-tertiary">Looking…</span>
          ) : (
            <>
              <p className="text-[12px] text-text-tertiary">{answer!.question}</p>
              <p className="mt-1 text-[14px] leading-snug text-text-primary">{answer!.answer}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
