// /join — see Nikki.md §2. The audio-test gate exists for one reason: iOS Safari will
// not speak until a user gesture has unlocked the speech API, and a worker who skips it
// hears nothing for the entire demo and never finds out why. So the gate is impossible
// to skip — "I'm ready" stays disabled until the test has been tapped at least once.
//
// This screen shows a callsign and a colour. It never shows the objective, the plan, or
// anyone else — the privacy premise starts here, not at the worker view.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useHiveSocket } from "../hooks/useHiveSocket";
import { useSpeech } from "../hooks/useSpeech";
import type { CommsProfile, Worker } from "../types/hive";

const TOKEN_KEY = "hive_token";
const BREATH_MS = 1500;
const INK = "#0b0b0c";

function readOrCreateToken(): string {
  const existing = localStorage.getItem(TOKEN_KEY);
  if (existing) return existing;
  const fresh = crypto.randomUUID();
  localStorage.setItem(TOKEN_KEY, fresh);
  return fresh;
}

export default function Join() {
  const navigate = useNavigate();
  const token = useMemo(readOrCreateToken, []);
  const { connected, lastMessage, send } = useHiveSocket("worker", token);
  const [identity, setIdentity] = useState<Worker | null>(null);
  const [silent, setSilent] = useState(false);
  const [audioTested, setAudioTested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [breath, setBreath] = useState(true);
  const { speak, supported } = useSpeech(identity?.callsign ?? "", silent);

  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type === "worker_assigned") {
      const payload = lastMessage.payload as { identity: Worker; token: string };
      setIdentity(payload.identity);
      localStorage.setItem(TOKEN_KEY, payload.token);
    }
    if (lastMessage.type === "state_snapshot") {
      const snap = lastMessage.payload as {
        identity: Worker | null;
        comms_profile: CommsProfile | null;
      };
      if (snap.identity) setIdentity(snap.identity);
      if (snap.comms_profile) setSilent(snap.comms_profile === "silent");
    }
    if (lastMessage.type === "error_event") {
      const payload = lastMessage.payload as { code: string; message: string };
      if (payload.code === "hive_full") setError(payload.message);
    }
  }, [lastMessage]);

  // Slow breathe on the waiting dot, driven by a transition rather than a keyframe loop
  // so it stops the moment a callsign lands.
  useEffect(() => {
    if (identity) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setBreath((b) => !b), BREATH_MS);
    return () => clearInterval(t);
  }, [identity]);

  const testAudio = useCallback(() => {
    if (identity) {
      speak(
        {
          id: `audio_test_${identity.id}_${Date.now()}`, // fresh id so a second tap replays
          action_id: "",
          worker_id: identity.id,
          display_text: "AUDIO CHECK",
          spoken_text: `Audio check. You are ${identity.callsign}.`,
          detail_text: "",
          urgency: "normal",
          expected_duration_seconds: 0,
          requires_verification: false,
          correction_text: null,
          issued_at: new Date().toISOString(),
        },
        { force: true },
      );
    }
    setAudioTested(true);
  }, [identity, speak]);

  function ready() {
    send("worker_ready");
    if (identity) navigate(`/worker/${identity.id}`);
  }

  if (error) {
    return (
      <div className="flex h-[100dvh] flex-col items-center justify-center gap-4 px-8 text-center">
        <div className="text-[14px] font-bold uppercase tracking-[0.3em] text-text-tertiary">
          Hive
        </div>
        <p className="max-w-[280px] text-[18px] leading-snug text-text-primary">{error}</p>
        <p className="max-w-[280px] text-[15px] leading-snug text-text-secondary">
          Every slot is taken. Ask the host to free one, then reload.
        </p>
      </div>
    );
  }

  // The audio gate is the whole point of this screen, except under a silent comms
  // profile where there is nothing to test and holding someone here would be theatre.
  const gateCleared = audioTested || silent;

  return (
    <div
      className="flex h-[100dvh] w-full select-none flex-col overflow-hidden"
      style={{ background: identity && !silent ? `${identity.color}14` : "transparent" }}
    >
      {identity && (
        <div style={{ height: 3, background: identity.color, flexShrink: 0 }} />
      )}

      <div className="flex min-h-0 flex-1 flex-col items-center px-6 pb-8 pt-5">
        <div className="shrink-0 text-[13px] font-bold uppercase tracking-[0.3em] text-text-tertiary">
          Hive
        </div>

        {!identity ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 text-center">
            <span
              style={{
                width: 20,
                height: 20,
                borderRadius: "50%",
                background: "var(--information)",
                opacity: breath ? 1 : 0.25,
                transform: breath ? "scale(1)" : "scale(0.82)",
                transition: `opacity ${BREATH_MS}ms ease-in-out, transform ${BREATH_MS}ms ease-in-out`,
              }}
            />
            <p className="text-[15px] uppercase tracking-[0.16em] text-text-secondary">
              {connected ? "Claiming a slot" : "Reconnecting"}
            </p>
          </div>
        ) : (
          <>
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-center">
              <div className="text-[12px] uppercase tracking-[0.22em] text-text-tertiary">
                You are
              </div>
              <div
                className="font-bold leading-none"
                style={{
                  color: identity.color,
                  fontSize: "clamp(44px, 16vw, 64px)",
                  letterSpacing: "-0.01em",
                }}
              >
                {identity.callsign}
              </div>
              <div className="text-[17px] text-text-secondary">
                {identity.role || identity.display_name}
              </div>
              <p className="mt-4 max-w-[280px] text-[15px] leading-snug text-text-secondary">
                This phone will show you one action at a time. Nothing else — not the
                plan, not anyone else&apos;s job.
              </p>
            </div>

            <div className="flex w-full shrink-0 flex-col gap-3">
              {!silent && (
                <>
                  <button
                    className="flex min-h-[68px] w-full items-center justify-center rounded-surface border border-separator-strong bg-surface-primary px-4 text-[17px] font-semibold tracking-[0.06em] text-text-primary"
                    onClick={testAudio}
                  >
                    {!supported
                      ? "NO AUDIO ON THIS PHONE — TAP TO CONTINUE"
                      : audioTested
                        ? "PLAY AGAIN"
                        : "TEST AUDIO"}
                  </button>
                  <p className="min-h-[36px] px-2 text-center text-[13px] leading-snug text-text-tertiary">
                    {!supported
                      ? "This browser has no speech. You'll get every instruction as text."
                      : audioTested
                        ? "Didn't hear it? Turn the volume up and flip the silent switch, then play again."
                        : "Tap it once — iPhones stay silent all demo until you do."}
                  </p>
                </>
              )}

              <button
                className="flex min-h-[80px] w-full items-center justify-center rounded-surface px-4 text-[20px] font-bold tracking-[0.06em]"
                style={
                  gateCleared
                    ? { background: identity.color, color: INK }
                    : { background: "var(--surface-secondary)", color: "var(--text-tertiary)" }
                }
                onClick={ready}
                disabled={!gateCleared}
              >
                I&apos;M READY
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
