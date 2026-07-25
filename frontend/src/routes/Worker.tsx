// /worker/:id — the worker phone. Nikki.md §2: never show the objective, the task
// graph, other workers, or how many actions remain. A worker who can infer the plan
// breaks the entire premise — this component receives ONLY its own instructions.
//
// silentMode is hardcoded false here: the campus-emergency comms_profile that would
// drive it lives on the scenario, and the minimal state_snapshot this backend spine
// sends doesn't carry scenario/lexicon yet (Ojas's/Steven's scope). Wiring it up is a
// single prop once that lands — see Nikki.md §6.

import { useEffect, useRef, useState } from "react";
import { useHiveSocket } from "../hooks/useHiveSocket";
import { useSpeech } from "../hooks/useSpeech";
import { useHaptics } from "../hooks/useHaptics";
import type { Instruction, WorkerIdentity } from "../types/hive";

const TOKEN_KEY = "hive_token";
const EMERGENCY_HOLD_MS = 800;

export default function Worker() {
  const token = localStorage.getItem(TOKEN_KEY);
  const { connected, reconnecting, lastMessage, send } = useHiveSocket("worker", token);
  const [identity, setIdentity] = useState<WorkerIdentity | null>(null);
  const [instruction, setInstruction] = useState<Instruction | null>(null);
  const [completedTapped, setCompletedTapped] = useState(false);
  const [emergencyArming, setEmergencyArming] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const { speak, supported } = useSpeech(identity?.callsign ?? "", false);
  const { buzz } = useHaptics();
  const emergencyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!lastMessage) return;
    switch (lastMessage.type) {
      case "worker_assigned": {
        const payload = lastMessage.payload as { identity: WorkerIdentity };
        setIdentity(payload.identity);
        break;
      }
      case "instruction_created": {
        const instr = lastMessage.payload as Instruction;
        setInstruction(instr);
        setCompletedTapped(false);
        speak(instr);
        buzz(instr.urgency);
        break;
      }
      case "instruction_cancelled": {
        setInstruction(null);
        setCompletedTapped(false);
        break;
      }
      default:
        break;
    }
  }, [lastMessage, speak, buzz]);

  function completed() {
    if (!instruction || completedTapped) return;
    setCompletedTapped(true); // don't wait for the round trip — an unresponsive-looking
    send("worker_completed", { action_id: instruction.action_id }); // button gets tapped 4 more times
  }

  function repeat() {
    if (instruction) speak(instruction, { force: true });
  }

  function cantDo() {
    if (!instruction) return;
    send("worker_blocked", { action_id: instruction.action_id, reason: "worker reported unable to complete" });
  }

  function help() {
    if (!instruction) return;
    send("worker_help", { action_id: instruction.action_id });
  }

  function pause() {
    send("worker_pause");
  }

  function startEmergencyHold() {
    setEmergencyArming(true);
    emergencyTimer.current = setTimeout(() => {
      send("worker_emergency");
      setEmergencyArming(false);
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
      <div className="worker-screen">
        <p className="worker-status">{connected ? "LINKING…" : "RECONNECTING…"}</p>
      </div>
    );
  }

  const timeoutFraction = instruction
    ? Math.max(
        0,
        1 - (now - Date.parse(instruction.issued_at)) / 1000 / instruction.expected_duration_seconds,
      )
    : 0;
  const remainingSeconds = instruction
    ? Math.max(0, Math.round(instruction.expected_duration_seconds * timeoutFraction))
    : 0;

  return (
    <div className="worker-screen" style={{ background: `${identity.color}14` }}>
      <div className="worker-header">
        <span className="worker-callsign" style={{ color: identity.color }}>
          {identity.callsign}
        </span>
        <span className="worker-link">
          {reconnecting ? "○ RECONNECTING" : connected ? "● LINKED" : "○ OFFLINE"}
        </span>
      </div>

      {!instruction ? (
        <div className="worker-idle">
          <div className="worker-pulse" style={{ background: identity.color }} />
          <div className="worker-standby">STAND BY</div>
          <div className="worker-substatus">HIVE is coordinating the next action.</div>
          {!supported && <div className="worker-chip">TTS unavailable — text only</div>}
        </div>
      ) : (
        <div className="worker-instruction">
          <div className="worker-display-text">{instruction.display_text}</div>
          {instruction.detail_text && <div className="worker-detail-text">{instruction.detail_text}</div>}
          <div className="worker-timeout-track">
            <div
              className="worker-timeout-fill"
              style={{ width: `${timeoutFraction * 100}%`, background: identity.color }}
            />
          </div>
          <div className="worker-timeout-label">{remainingSeconds}s</div>
        </div>
      )}

      <div className="worker-actions">
        <button
          className="worker-btn worker-btn-primary"
          style={{ background: instruction ? identity.color : "var(--fg-2)" }}
          disabled={!instruction || completedTapped}
          onClick={completed}
        >
          {completedTapped ? "✓ REPORTED — AWAITING VERIFICATION" : "COMPLETED"}
        </button>
        <div className="worker-btn-row">
          <button className="worker-btn worker-btn-secondary" disabled={!instruction} onClick={repeat}>
            REPEAT
          </button>
          <button className="worker-btn worker-btn-secondary" disabled={!instruction} onClick={help}>
            HELP
          </button>
        </div>
        <div className="worker-btn-row">
          <button className="worker-btn worker-btn-secondary" disabled={!instruction} onClick={cantDo}>
            CAN&apos;T DO
          </button>
          <button className="worker-btn worker-btn-secondary" onClick={pause}>
            PAUSE
          </button>
        </div>
        <button
          className={`worker-btn worker-btn-emergency ${emergencyArming ? "arming" : ""}`}
          onPointerDown={startEmergencyHold}
          onPointerUp={cancelEmergencyHold}
          onPointerLeave={cancelEmergencyHold}
        >
          {emergencyArming ? "HOLD…" : "EMERGENCY (HOLD)"}
        </button>
      </div>
    </div>
  );
}
