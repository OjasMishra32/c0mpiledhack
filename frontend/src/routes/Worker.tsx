// /worker/:id — the worker phone. Nikki.md §2: never show the objective, the task
// graph, other workers, or how many actions remain. A worker who can infer the plan
// breaks the entire premise, so this component renders exactly one thing: the single
// instruction addressed to this phone.
//
// Held at arm's length, in a noisy room, for under two seconds. Every decision below
// falls out of that: 32–40px instruction text, 64px+ targets in the thumb half, one
// viewport with no scroll ever (100dvh — 100vh loses ~60px to mobile Safari's toolbar
// and hides the COMPLETED button), and no motion that isn't reporting a state change.

import { useCallback, useEffect, useRef, useState } from "react";
import { useHiveSocket } from "../hooks/useHiveSocket";
import { useSpeech } from "../hooks/useSpeech";
import { useHaptics } from "../hooks/useHaptics";
import type { CommsProfile, Instruction, Worker as WorkerIdentity } from "../types/hive";

const TOKEN_KEY = "hive_token";
const EMERGENCY_HOLD_MS = 800;
const NOTICE_MS = 4000;
const BREATH_MS = 1500;
const INK = "#0b0b0c"; // text on a filled identity-colour control

/** The worker-scoped snapshot from websocket_manager.send_snapshot — this phone only. */
interface WorkerSnapshot {
  identity: WorkerIdentity | null;
  execution_status: string | null;
  comms_profile: CommsProfile | null;
  instruction: Instruction | null;
}

// Minimal local shapes for the Wake Lock API. Declared here rather than leaned on from
// lib.dom so this compiles the same on every TypeScript version in the room.
interface WakeLockSentinelLike {
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
}
interface WakeLockLike {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
}

/**
 * Keep the screen awake for the length of the run. A phone that dims mid-action is a
 * worker who misses their instruction, and on stage nobody notices until it's too late.
 * The OS drops the lock every time the tab hides, so it is re-requested on
 * visibilitychange. Fully feature-detected; a rejected request is normal (backgrounded
 * tab, low battery) and must never throw or surface.
 */
function useWakeLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const api = (navigator as unknown as { wakeLock?: WakeLockLike }).wakeLock;
    if (!api) return; // Safari < 16.4 and older Android — degrade in silence

    let disposed = false;
    let sentinel: WakeLockSentinelLike | null = null;

    const acquire = () => {
      if (disposed || sentinel || document.visibilityState !== "visible") return;
      api
        .request("screen")
        .then((next) => {
          if (disposed) {
            void next.release().catch(() => {});
            return;
          }
          sentinel = next;
          next.addEventListener("release", () => {
            sentinel = null;
          });
        })
        .catch(() => {
          // denied, or the tab lost focus mid-request — expected, never surfaced
        });
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") acquire();
    };

    acquire();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisibility);
      void sentinel?.release().catch(() => {});
      sentinel = null;
    };
  }, [active]);
}

/** Seven words is the ceiling on the primary line, so the size steps with word count. */
function instructionSize(text: string): string {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  if (words <= 4) return "clamp(32px, 11.5vw, 40px)";
  if (words <= 7) return "clamp(30px, 10vw, 36px)";
  return "clamp(26px, 8.5vw, 32px)";
}

const SECONDARY_BTN =
  "flex min-h-[68px] flex-1 items-center justify-center rounded-surface border " +
  "border-separator-strong bg-surface-primary px-3 text-[16px] font-semibold " +
  "tracking-[0.06em] text-text-primary disabled:opacity-30";

function Chip({ label, tone = "var(--text-tertiary)" }: { label: string; tone?: string }) {
  return (
    <span
      className="rounded-control border px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.12em]"
      style={{ borderColor: tone, color: tone }}
    >
      {label}
    </span>
  );
}

export default function Worker() {
  const token = localStorage.getItem(TOKEN_KEY);
  const { connected, reconnecting, lastMessage, send } = useHiveSocket("worker", token);

  const [identity, setIdentity] = useState<WorkerIdentity | null>(null);
  const [instruction, setInstruction] = useState<Instruction | null>(null);
  const [silent, setSilent] = useState(false);
  const [reported, setReported] = useState(false);
  const [selfPaused, setSelfPaused] = useState(false);
  const [hostPaused, setHostPaused] = useState(false);
  const [halted, setHalted] = useState(false);
  const [emergencyArming, setEmergencyArming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [breath, setBreath] = useState(true);

  const { speak, cancel: cancelSpeech, supported: ttsSupported } = useSpeech(
    identity?.callsign ?? "",
    silent,
  );
  const { buzz, stop: stopBuzz } = useHaptics(!silent);
  useWakeLock(true);

  const emergencyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handledRef = useRef<unknown>(null);
  const currentIdRef = useRef<string | null>(null);

  const flash = useCallback((text: string) => {
    setNotice(text);
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  useEffect(
    () => () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      if (emergencyTimer.current) clearTimeout(emergencyTimer.current);
    },
    [],
  );

  // Adopting an instruction: speak it, buzz it, acknowledge it, restart the countdown.
  // Everything is keyed on instruction.id so a re-delivery (refresh mid-action, or the
  // snapshot that follows a reconnect) lands silently instead of shouting a second time.
  const adopt = useCallback(
    (next: Instruction | null) => {
      const nextId = next?.id ?? null;
      const changed = nextId !== currentIdRef.current;
      currentIdRef.current = nextId;
      setInstruction(next);
      if (!next) {
        setReported(false);
        cancelSpeech();
        stopBuzz();
        return;
      }
      if (!changed) return;
      setReported(false);
      // The countdown is anchored to arrival on *this* device, not to issued_at: five
      // phones with five slightly wrong clocks must not show five different timers.
      setStartedAt(Date.now());
      setNow(Date.now());
      speak(next);
      buzz(next.urgency);
      send("worker_acknowledged", { action_id: next.action_id });
    },
    [speak, buzz, send, cancelSpeech, stopBuzz],
  );

  useEffect(() => {
    if (!lastMessage || lastMessage === handledRef.current) return;
    handledRef.current = lastMessage; // envelope.seq is always 0 on the wire — dedupe by identity

    switch (lastMessage.type) {
      case "worker_assigned": {
        const payload = lastMessage.payload as { identity: WorkerIdentity };
        setIdentity(payload.identity);
        setSelfPaused(payload.identity.status === "paused");
        break;
      }
      case "state_snapshot": {
        // Worker snapshots are only broadcast on a reset, and they are scoped by the
        // server to this phone. Taking the pause flag from the server rather than local
        // state means a refresh — or a reset — never strands someone paused.
        const snap = lastMessage.payload as WorkerSnapshot;
        if (snap.identity) {
          setIdentity(snap.identity);
          setSelfPaused(snap.identity.status === "paused");
        }
        if (snap.comms_profile) setSilent(snap.comms_profile === "silent");
        if (snap.execution_status) {
          setHostPaused(snap.execution_status === "paused");
          setHalted(snap.execution_status === "emergency");
        }
        adopt(snap.instruction ?? null);
        break;
      }
      case "instruction_created": {
        setHalted(false);
        adopt(lastMessage.payload as Instruction);
        break;
      }
      case "instruction_cancelled": {
        adopt(null);
        flash("Stand down — that action was withdrawn.");
        break;
      }
      case "execution_paused": {
        setHostPaused(true);
        break;
      }
      case "execution_started":
      case "execution_resumed": {
        setHostPaused(false);
        setHalted(false);
        break;
      }
      case "emergency_stop": {
        setHalted(true);
        adopt(null);
        break;
      }
      // `event` is broadcast to every socket including this one and its message text can
      // name another worker's action. It is deliberately never rendered here — see the
      // note in the final report.
      default:
        break;
    }
  }, [lastMessage, adopt, flash]);

  const duration = instruction?.expected_duration_seconds ?? 0;
  const timed = !!instruction && duration > 0 && !reported;

  useEffect(() => {
    if (!timed) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [timed, startedAt]);

  // The idle dot breathes on a slow interval with a matching transition rather than a
  // keyframe loop: it stops dead the moment an instruction lands, and honours
  // prefers-reduced-motion by simply not starting.
  useEffect(() => {
    if (instruction || halted) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setBreath(true);
      return;
    }
    const t = setInterval(() => setBreath((b) => !b), BREATH_MS);
    return () => clearInterval(t);
  }, [instruction, halted]);

  const elapsed = (now - startedAt) / 1000;
  const fraction = timed ? Math.min(1, Math.max(0, 1 - elapsed / duration)) : 0;
  const remaining = timed ? Math.max(0, Math.ceil(duration * fraction)) : 0;

  function completed() {
    if (!instruction || reported) return;
    // Optimistic on purpose. Waiting for the round trip makes the button look dead, and
    // a button that looks dead gets tapped four more times — four duplicate reports.
    setReported(true);
    stopBuzz();
    cancelSpeech();
    send("worker_completed", { action_id: instruction.action_id });
  }

  function repeat() {
    if (instruction) speak(instruction, { force: true }); // purely local; the server is not involved
  }

  function help() {
    if (!instruction) return;
    send("worker_help", { action_id: instruction.action_id });
    flash("Help requested — someone is coming.");
  }

  function cantDo() {
    if (!instruction) return;
    send("worker_blocked", {
      action_id: instruction.action_id,
      reason: "worker reported unable to complete",
    });
    setReported(false);
    flash("Reported — HIVE is re-planning around it.");
  }

  function togglePause() {
    if (selfPaused) {
      send("worker_resume");
      setSelfPaused(false);
      flash("Back in. You'll get the next action.");
    } else {
      send("worker_pause");
      setSelfPaused(true);
      cancelSpeech();
      flash("Paused — no new actions will come to you.");
    }
  }

  function startEmergencyHold() {
    setEmergencyArming(true);
    emergencyTimer.current = setTimeout(() => {
      setEmergencyArming(false);
      setHalted(true);
      cancelSpeech();
      buzz("critical");
      send("worker_emergency");
    }, EMERGENCY_HOLD_MS);
  }

  function cancelEmergencyHold() {
    setEmergencyArming(false);
    if (emergencyTimer.current) {
      clearTimeout(emergencyTimer.current);
      emergencyTimer.current = null;
    }
  }

  if (!identity) {
    return (
      <div className="flex h-[100dvh] flex-col items-center justify-center gap-4 px-8 text-center">
        <span
          className="inline-block"
          style={{
            width: 14,
            height: 14,
            borderRadius: "50%",
            background: "var(--information)",
            opacity: breath ? 1 : 0.3,
            transition: `opacity ${BREATH_MS}ms ease-in-out`,
          }}
        />
        <p className="text-[15px] uppercase tracking-[0.16em] text-text-secondary">
          {connected ? "Linking…" : "Reconnecting…"}
        </p>
      </div>
    );
  }

  const link = reconnecting ? "Reconnecting" : connected ? "Linked" : "Offline";
  const linkTone = connected && !reconnecting ? "var(--success)" : "var(--warning)";
  const correction = !!instruction?.correction_text;
  const critical = instruction?.urgency === "critical";

  return (
    <div
      className="flex h-[100dvh] w-full select-none flex-col overflow-hidden"
      // The wash is the identity colour at low opacity: a half-second glance confirms
      // whose phone this is. Silent mode drops it for a minimum-brightness black.
      style={{ background: silent ? "#000000" : `${identity.color}14` }}
    >
      <div style={{ height: 3, background: identity.color, flexShrink: 0 }} />

      <div className="flex min-h-0 flex-1 flex-col px-4 pb-6 pt-3">
        <header className="flex shrink-0 items-center justify-between gap-3 text-[12px] uppercase tracking-[0.14em]">
          <span className="flex min-w-0 items-center gap-2">
            <span
              className="shrink-0"
              style={{ width: 9, height: 9, borderRadius: "50%", background: identity.color }}
            />
            <span className="font-bold" style={{ color: identity.color }}>
              {identity.callsign}
            </span>
            <span className="truncate text-text-tertiary">{identity.role}</span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-text-tertiary">
            <span
              style={{ width: 7, height: 7, borderRadius: "50%", background: linkTone }}
            />
            {link}
          </span>
        </header>

        {(silent || hostPaused || selfPaused || !ttsSupported) && (
          <div className="mt-3 flex shrink-0 flex-wrap gap-2">
            {silent && <Chip label="Silent" />}
            {hostPaused && <Chip label="Hive held" tone="var(--warning)" />}
            {selfPaused && <Chip label="You are paused" tone="var(--warning)" />}
            {!ttsSupported && !silent && <Chip label="Text only" />}
          </div>
        )}

        {halted ? (
          <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 text-center">
            <div
              className="text-[40px] font-bold tracking-[0.06em]"
              style={{ color: "var(--failure)" }}
            >
              ALL STOP
            </div>
            <p className="max-w-[280px] text-[16px] leading-snug text-text-secondary">
              Put everything down and stand still. Wait for the host.
            </p>
          </main>
        ) : !instruction ? (
          <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 text-center">
            <span
              style={{
                width: 22,
                height: 22,
                borderRadius: "50%",
                background: identity.color,
                opacity: breath ? 1 : 0.25,
                transform: breath ? "scale(1)" : "scale(0.82)",
                transition: `opacity ${BREATH_MS}ms ease-in-out, transform ${BREATH_MS}ms ease-in-out`,
              }}
            />
            <div className="text-[22px] font-semibold tracking-[0.18em] text-text-primary">
              STAND BY
            </div>
            <p className="max-w-[260px] text-[16px] leading-snug text-text-secondary">
              {selfPaused
                ? "You are paused. Tap RESUME ME when you're ready."
                : "HIVE is coordinating the next action."}
            </p>
          </main>
        ) : (
          <main
            className="flex min-h-0 flex-1 flex-col justify-center overflow-hidden"
            aria-live="assertive"
            aria-atomic="true"
          >
            {(correction || critical || reported) && (
              <div className="mb-3 flex shrink-0 items-center gap-2">
                {reported ? (
                  <Chip label="Reported — verifying" tone="var(--success)" />
                ) : correction ? (
                  <Chip label="Correction" tone="var(--warning)" />
                ) : (
                  <Chip label="Critical" tone="var(--failure)" />
                )}
              </div>
            )}

            {/* The one state-change transition in the app: the instruction recedes the
                instant COMPLETED is tapped, so the tap is visibly registered. */}
            <div
              className="min-h-0 transition-opacity duration-200 ease-standard"
              style={{ opacity: reported ? 0.35 : 1 }}
            >
              <p
                className="text-text-primary"
                style={{
                  fontSize: instructionSize(instruction.display_text),
                  fontWeight: 600,
                  lineHeight: 1.08,
                  letterSpacing: "-0.01em",
                }}
              >
                {instruction.display_text}
              </p>
              {instruction.detail_text && (
                <p className="mt-4 text-[16px] leading-snug text-text-secondary">
                  {instruction.detail_text}
                </p>
              )}
            </div>

            {timed && (
              <div className="mt-6 shrink-0">
                <div
                  className="h-[6px] w-full overflow-hidden"
                  style={{ borderRadius: 3, background: "var(--separator)" }}
                >
                  <div
                    className="h-full transition-[width] duration-200 ease-linear"
                    style={{
                      width: `${fraction * 100}%`,
                      background: fraction < 0.2 ? "var(--warning)" : identity.color,
                    }}
                  />
                </div>
                <div className="mt-2 text-right font-technical text-[13px] text-text-tertiary">
                  {remaining}s
                </div>
              </div>
            )}
          </main>
        )}

        {notice && (
          <p className="mt-3 shrink-0 text-center text-[14px] leading-snug text-text-secondary">
            {notice}
          </p>
        )}

        {!halted && (
          <div className="mt-4 flex shrink-0 flex-col gap-[10px]">
            <button
              className="flex min-h-[80px] w-full items-center justify-center rounded-surface px-4 text-[20px] font-bold tracking-[0.06em]"
              style={
                reported
                  ? {
                      background: "transparent",
                      border: "1px solid var(--success)",
                      color: "var(--success)",
                    }
                  : !instruction
                    ? {
                        background: "var(--surface-secondary)",
                        color: "var(--text-tertiary)",
                      }
                    : silent
                      ? {
                          background: "transparent",
                          border: `2px solid ${identity.color}`,
                          color: identity.color,
                        }
                      : { background: identity.color, color: INK }
              }
              disabled={!instruction || reported}
              onClick={completed}
            >
              {reported ? "REPORTED — VERIFYING" : "COMPLETED"}
            </button>

            <div className="flex gap-[10px]">
              <button className={SECONDARY_BTN} disabled={!instruction} onClick={repeat}>
                REPEAT
              </button>
              <button className={SECONDARY_BTN} disabled={!instruction} onClick={help}>
                NEED HELP
              </button>
            </div>

            <div className="flex gap-[10px]">
              <button
                className={SECONDARY_BTN}
                style={{ color: "var(--warning)" }}
                disabled={!instruction}
                onClick={cantDo}
              >
                CAN&apos;T DO
              </button>
              <button className={SECONDARY_BTN} onClick={togglePause}>
                {selfPaused ? "RESUME ME" : "PAUSE ME"}
              </button>
            </div>

            {/* Long-press only. A single tap must never be able to halt five people, and
                the fill is the confirmation that the hold is being counted. Deliberately
                the shortest control on the screen — it is the one you should not hit. */}
            <button
              className="relative flex min-h-[56px] w-full items-center justify-center overflow-hidden rounded-surface text-[14px] font-semibold tracking-[0.14em]"
              style={{
                border: "1px solid var(--failure)",
                color: emergencyArming ? "#ffffff" : "var(--failure)",
                background: "transparent",
                touchAction: "none",
                WebkitUserSelect: "none",
                WebkitTouchCallout: "none",
              }}
              aria-label="Emergency stop — press and hold"
              onPointerDown={startEmergencyHold}
              onPointerUp={cancelEmergencyHold}
              onPointerLeave={cancelEmergencyHold}
              onPointerCancel={cancelEmergencyHold}
              onContextMenu={(e) => e.preventDefault()}
            >
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-0"
                style={{
                  width: emergencyArming ? "100%" : "0%",
                  background: "var(--failure)",
                  transition: `width ${emergencyArming ? EMERGENCY_HOLD_MS : 150}ms linear`,
                }}
              />
              <span className="relative">
                {emergencyArming ? "KEEP HOLDING…" : "EMERGENCY — HOLD"}
              </span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
