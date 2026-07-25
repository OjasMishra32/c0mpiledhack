// /join — see Nikki.md §2. The audio-test gate exists for one reason: iOS Safari
// will not speak until a user gesture has unlocked the speech API, so "Test Audio"
// is impossible to skip — it gates the Ready button.

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useHiveSocket } from "../hooks/useHiveSocket";
import { useSpeech } from "../hooks/useSpeech";
import type { WorkerIdentity } from "../types/hive";

const TOKEN_KEY = "hive_token";

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
  const [identity, setIdentity] = useState<WorkerIdentity | null>(null);
  const [audioTested, setAudioTested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { speak, supported } = useSpeech(identity?.callsign ?? "", false);

  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type === "worker_assigned") {
      const payload = lastMessage.payload as { identity: WorkerIdentity; token: string };
      setIdentity(payload.identity);
      localStorage.setItem(TOKEN_KEY, payload.token);
    }
    if (lastMessage.type === "error_event") {
      const payload = lastMessage.payload as { code: string; message: string };
      if (payload.code === "hive_full") setError(payload.message);
    }
  }, [lastMessage]);

  function testAudio() {
    if (identity) {
      speak(
        {
          id: `audio_test_${identity.id}`,
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
  }

  function ready() {
    send("worker_ready");
    if (identity) navigate(`/worker/${identity.id}`);
  }

  if (error) {
    return (
      <div className="join-screen">
        <h1>HIVE</h1>
        <p className="join-status">{error}</p>
      </div>
    );
  }

  return (
    <div className="join-screen">
      <h1>HIVE</h1>
      {!identity ? (
        <p className="join-status">
          <span className="join-dot" /> {connected ? "CONNECTING TO COLLECTIVE" : "RECONNECTING"}
        </p>
      ) : (
        <>
          <div className="join-identity" style={{ color: identity.color }}>
            <div className="join-callsign">{identity.callsign}</div>
            <div className="join-role">{identity.role || identity.display_name}</div>
          </div>
          <button className="join-btn join-btn-secondary" onClick={testAudio}>
            {supported ? "TEST AUDIO" : "AUDIO UNAVAILABLE — TAP TO CONTINUE"}
          </button>
          <button
            className="join-btn join-btn-primary"
            style={{ background: identity.color }}
            onClick={ready}
            disabled={!audioTested}
          >
            I&apos;M READY
          </button>
        </>
      )}
    </div>
  );
}
