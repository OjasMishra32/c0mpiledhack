// Minimal stub of Ojas's hook (frontend/src/hooks/useHiveSocket.ts is his file) —
// built only far enough that Join.tsx/Worker.tsx have something real to run against.
// Contract from David.md §9: auto-reconnect with backoff (250ms -> 4s), dedupe
// messages by `seq`, one immutable message replacing the last on each update.

import { useCallback, useEffect, useRef, useState } from "react";
import type { WsEnvelope } from "../types/hive";

export type SocketRole = "host" | "worker";

interface UseHiveSocketResult {
  connected: boolean;
  reconnecting: boolean;
  lastMessage: WsEnvelope | null;
  send: (type: string, payload?: Record<string, unknown>) => void;
}

export function useHiveSocket(role: SocketRole, token?: string | null): UseHiveSocketResult {
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [lastMessage, setLastMessage] = useState<WsEnvelope | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(250);
  const seenSeq = useRef<Set<number>>(new Set());
  const unmountedRef = useRef(false);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams({ role });
    if (token) params.set("token", token);
    const sock = new WebSocket(`${proto}://${window.location.host}/ws?${params.toString()}`);
    wsRef.current = sock;

    sock.onopen = () => {
      setConnected(true);
      setReconnecting(false);
      backoffRef.current = 250;
    };
    sock.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      if (unmountedRef.current) return;
      setReconnecting(true);
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, 4000);
      setTimeout(connect, delay);
    };
    sock.onerror = () => sock.close();
    sock.onmessage = (event: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(event.data) as WsEnvelope;
        if (msg.seq && seenSeq.current.has(msg.seq)) return;
        if (msg.seq) seenSeq.current.add(msg.seq);
        setLastMessage(msg);
      } catch {
        // malformed frame — drop it, never crash the socket loop
      }
    };
  }, [role, token]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    const sock = wsRef.current;
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type, payload }));
    }
  }, []);

  return { connected, reconnecting, lastMessage, send };
}
