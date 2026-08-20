"""Call this from the vision Raspberry Pi after detecting the cycle-end sign."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid


def send_cycle_complete_stop(control_url: str, token: str, timeout_s: float = 2.0) -> dict:
    """Latch the PiRacer in STOPPED state; only a later web START restarts it."""
    payload = {
        "command": "STOP",
        "command_id": str(uuid.uuid4()),
        "sent_at_ms": int(time.time() * 1000),
        "source": "VISION",
        "reason": "CYCLE_COMPLETE_SIGN",
    }
    request = urllib.request.Request(
        control_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))
