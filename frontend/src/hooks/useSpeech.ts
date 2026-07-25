// Speak exactly once. The single most common bug class here: React re-renders,
// naive code re-speaks, five phones start chanting over each other. Nikki.md §2.

import { useCallback, useEffect, useRef } from "react";
import type { Instruction } from "../types/hive";

function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  return (
    voices.find((v) => v.lang === "en-US" && v.localService) ??
    voices.find((v) => v.lang.startsWith("en")) ??
    voices[0]
  );
}

export function useSpeech(callsign: string, silentMode: boolean) {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const spokenIds = useRef<Set<string>>(new Set());
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (!supported) return;
    const load = () => {
      voicesRef.current = window.speechSynthesis.getVoices();
    };
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", load);
  }, [supported]);

  const speak = useCallback(
    (instr: Instruction, opts: { force?: boolean } = {}) => {
      const { force = false } = opts;
      if (!force && spokenIds.current.has(instr.id)) return; // the guard
      spokenIds.current.add(instr.id);
      if (silentMode || !supported) return;

      const critical = instr.urgency === "critical";
      const text = critical ? `${callsign}. ${instr.spoken_text}` : instr.spoken_text;
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = critical ? 1.15 : 1.05;
      utterance.pitch = 1.0;
      utterance.volume = 1.0;
      const voice = pickVoice(voicesRef.current);
      if (voice) utterance.voice = voice;

      window.speechSynthesis.cancel(); // never queue; a correction mid-sentence must win instantly
      window.speechSynthesis.speak(utterance);
    },
    [callsign, silentMode, supported],
  );

  return { speak, supported };
}
