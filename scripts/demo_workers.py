#!/usr/bin/env python
"""Fill worker slots with local clients for filming.

These are ordinary worker clients speaking the ordinary protocol — the same join,
acknowledge and complete messages a phone sends. They exist so the collective is full when
only a few real phones are on hand, and so a run finishes on camera without depending on
five people tapping in time.

Real phones always take priority: a phone that joins with its own token claims a free slot
first, and these only occupy whatever is left over. Run with --hold to have them connect
and stay idle instead of completing work.

    python scripts/demo_workers.py            # fill 5 slots, auto-complete
    python scripts/demo_workers.py -n 2       # only fill 2
    python scripts/demo_workers.py --hold     # connect but never complete
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets  (or use backend/.venv/bin/python)")

URL = "ws://localhost:8000/ws?role=worker&token=demo-{i}"


async def run_worker(i: int, delay: float, hold: bool) -> None:
    while True:
        try:
            async with websockets.connect(URL.format(i=i), ping_interval=None) as sock:
                callsign = "?"
                pending: list[tuple[float, str]] = []

                async def pump() -> None:
                    # Completing on a timer keeps the socket's read loop free, so the
                    # connection never looks dead while an action is in flight.
                    while True:
                        await asyncio.sleep(0.2)
                        now = asyncio.get_event_loop().time()
                        for due, action_id in list(pending):
                            if now >= due:
                                pending.remove((due, action_id))
                                await sock.send(
                                    json.dumps(
                                        {
                                            "type": "worker_completed",
                                            "payload": {"action_id": action_id},
                                        }
                                    )
                                )
                                print(f"  {callsign}: completed {action_id}")

                pumper = asyncio.create_task(pump())
                try:
                    async for raw in sock:
                        msg = json.loads(raw)
                        kind = msg.get("type")

                        if kind == "worker_assigned":
                            callsign = msg["payload"]["identity"]["callsign"]
                            print(f"{callsign} online")
                            await sock.send(json.dumps({"type": "worker_ready", "payload": {}}))

                        elif kind == "instruction_created":
                            p = msg["payload"]
                            print(f"  {callsign}: {p['display_text']}")
                            await sock.send(
                                json.dumps(
                                    {
                                        "type": "worker_acknowledged",
                                        "payload": {
                                            "action_id": p["action_id"],
                                            "instruction_id": p["id"],
                                        },
                                    }
                                )
                            )
                            if not hold:
                                pending.append(
                                    (asyncio.get_event_loop().time() + delay, p["action_id"])
                                )

                        elif kind == "error_event":
                            # Every slot taken by real phones — nothing to do here.
                            print(f"slot {i}: {msg['payload'].get('message')}")
                            return
                finally:
                    pumper.cancel()
        except Exception:
            await asyncio.sleep(1.5)  # server restarted mid-demo; just reconnect


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="how many slots to fill")
    ap.add_argument("--delay", type=float, default=3.5, help="seconds before completing")
    ap.add_argument("--hold", action="store_true", help="connect but never complete")
    args = ap.parse_args()

    print(f"connecting {args.n} worker(s)… Ctrl-C to stop\n")
    await asyncio.gather(*(run_worker(i, args.delay, args.hold) for i in range(args.n)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
