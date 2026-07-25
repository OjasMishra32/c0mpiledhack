// Speak exactly once. The single most common bug class here: React re-renders,
// naive code re-speaks, five phones start chanting over each other. Nikki.md §2.
//
// Three rules hold this together:
//   1. Every utterance is keyed on instruction.id and remembered in a ref Set. A ref,
//      not state — a Set in state would re-render, re-run the effect, and re-speak.
//   2. cancel() before every speak(). A correction that arrives mid-sentence must kill
//      the stale line instantly; a stale instruction is worse than silence.
//   3. Voices load asynchronously in Chrome. Without the 'voiceschanged' subscription
//      the first utterance of the demo — the one everyone is listening to — uses the
//      robotic default.

import { useCallback, useEffect, useRef, useState } from "react";
import type { Instruction } from "../types/hive";

// Chrome/Safari will happily hand back a novelty voice as voices[0]. These are the
// natural-sounding voices that actually ship on the devices in the room; first hit wins.
const PREFERRED = ["samantha", "google us english", "alex", "karen", "daniel"];

function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  const english = voices.filter((v) => v.lang.toLowerCase().startsWith("en"));
  for (const name of PREFERRED) {
    const hit = english.find((v) => v.name.toLowerCase().includes(name));
    if (hit) return hit;
  }
  return english.find((v) => v.lang === "en-US") ?? english[0] ?? voices[0];
}

export function useSpeech(callsign: string, silentMode: boolean) {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const spokenIds = useRef<Set<string>>(new Set());
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    if (!supported) return;
    const load = () => {
      voicesRef.current = window.speechSynthesis.getVoices();
    };
    load(); // Safari populates synchronously; Chrome returns [] here and fires below.
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", load);
  }, [supported]);

  // Leaving the route mid-sentence must not leave a voice talking over the room.
  useEffect(() => {
    if (!supported) return;
    return () => window.speechSynthesis.cancel();
  }, [supported]);

  const cancel = useCallback(() => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [supported]);

  const speak = useCallback(
    (instr: Instruction, opts: { force?: boolean } = {}) => {
      const { force = false } = opts;
      if (!force && spokenIds.current.has(instr.id)) return; // the guard
      spokenIds.current.add(instr.id);
      if (silentMode || !supported) return;

      // A recovery instruction carries correction_text already prefixed "Correction." —
      // reading the superseded line instead would send someone the wrong way.
      const body = (instr.correction_text || instr.spoken_text || "").trim();
      if (!body) return;

      const critical = instr.urgency === "critical";
      const named = callsign && body.toLowerCase().startsWith(callsign.toLowerCase());
      const text = critical && callsign && !named ? `${callsign}. ${body}` : body;

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = critical ? 1.15 : 1.05;
      utterance.pitch = 1.0;
      utterance.volume = 1.0;
      const voice = pickVoice(voicesRef.current);
      if (voice) {
        utterance.voice = voice;
        utterance.lang = voice.lang;
      }
      utterance.onstart = () => setSpeaking(true);
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);

      window.speechSynthesis.cancel(); // never queue; a correction mid-sentence must win instantly
      window.speechSynthesis.resume(); // Chrome parks the queue when the tab is backgrounded
      window.speechSynthesis.speak(utterance);
    },
    [callsign, silentMode, supported],
  );

  return { speak, cancel, speaking, supported };
}
