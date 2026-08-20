"""File-backed autonomous-control parts for the PiRacer DonkeyCar loop.

``dashboard_server.py`` is the only HTTP-facing process. It writes commands
atomically to ``control_state.json``; these parts only read that file, select
Local Pilot, and safely gate the final actuator values.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path


class CommandRejected(ValueError):
    pass


class ControlStateStore:
    VALID_COMMANDS = {"START", "STOP", "HEARTBEAT"}

    def __init__(self, path: Path, heartbeat_timeout_s: float, max_speed_mps: float,
                 clock_ms: Callable[[], int] | None = None) -> None:
        if heartbeat_timeout_s <= 0 or max_speed_mps <= 0:
            raise ValueError("heartbeat timeout and max speed must be positive")
        self.path = path
        self.heartbeat_timeout_ms = int(heartbeat_timeout_s * 1000)
        self.max_speed_mps = max_speed_mps
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def apply(self, payload: dict) -> dict:
        command, command_id = payload.get("command"), payload.get("command_id")
        if command not in self.VALID_COMMANDS:
            raise CommandRejected("unknown command")
        if not isinstance(command_id, str) or not command_id:
            raise CommandRejected("command_id is required")
        now, previous = self._clock_ms(), self._read_raw()
        state, target_speed = previous["state"], previous["target_speed_mps"]
        heartbeat = previous["last_heartbeat_ms"]
        if command == "START":
            speed = payload.get("target_speed_mps")
            if not isinstance(speed, (int, float)) or isinstance(speed, bool):
                raise CommandRejected("target_speed_mps is required")
            if not 0 < float(speed) <= self.max_speed_mps:
                raise CommandRejected(f"target_speed_mps must be greater than 0 and at most {self.max_speed_mps:.3f}")
            state, target_speed, heartbeat = "RUNNING", float(speed), now
        elif command == "STOP":
            state, target_speed, heartbeat = "STOPPED", 0.0, now
        elif state == "RUNNING":
            heartbeat = now
        value = {"version": 1, "state": state, "target_speed_mps": target_speed,
                 "last_heartbeat_ms": heartbeat, "updated_at_ms": now, "command_id": command_id}
        self._write_raw(value)
        return self._public_snapshot(value, now)

    def snapshot(self) -> dict:
        return self._public_snapshot(self._read_raw(), self._clock_ms())

    def _read_raw(self) -> dict:
        default = {"version": 1, "state": "STOPPED", "target_speed_mps": 0.0,
                   "last_heartbeat_ms": 0, "updated_at_ms": 0, "command_id": None}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("state") not in {"RUNNING", "STOPPED"}:
                return default
            if not isinstance(value.get("target_speed_mps"), (int, float)):
                return default
            if not isinstance(value.get("last_heartbeat_ms"), int):
                return default
            return {**default, **value}
        except (OSError, json.JSONDecodeError):
            return default

    def _write_raw(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _public_snapshot(self, value: dict, now: int) -> dict:
        stale = value["state"] == "RUNNING" and now - value["last_heartbeat_ms"] > self.heartbeat_timeout_ms
        state = "STOPPED" if stale else value["state"]
        target = 0.0 if state == "STOPPED" else float(value["target_speed_mps"])
        return {"accepted": True, "state": state,
                "mode": "LOCAL_PILOT" if state == "RUNNING" else "STOPPED",
                "target_speed_mps": target, "max_speed_mps": self.max_speed_mps,
                "heartbeat_timeout_s": self.heartbeat_timeout_ms / 1000,
                "command_id": value.get("command_id")}


class DashboardControlPart:
    """Read file state every vehicle loop and select Local Pilot when alive."""

    def __init__(self, state_store: ControlStateStore, max_throttle: float) -> None:
        if not 0 < max_throttle <= 1:
            raise ValueError("max_throttle must be greater than 0 and at most 1")
        self._state_store, self._max_throttle = state_store, max_throttle

    def run(self, _user_angle, _user_throttle, _user_mode) -> tuple[float, float, str, bool, float, float]:
        state = self._state_store.snapshot()
        if state["state"] != "RUNNING":
            return 0.0, 0.0, "user", False, 0.0, self._max_throttle
        scale = state["target_speed_mps"] / state["max_speed_mps"]
        return 0.0, 0.0, "local", True, min(max(scale, 0.0), 1.0), self._max_throttle


class DashboardDriveControl:
    """Last actuator gate: a stopped file state can never reach the motors."""

    def run(self, angle, throttle, active, max_throttle) -> tuple[float, float]:
        if not active:
            return 0.0, 0.0
        safe_angle = min(max(float(angle or 0.0), -1.0), 1.0)
        limit = min(max(float(max_throttle), 0.0), 1.0)
        return safe_angle, min(max(float(throttle or 0.0), -limit), limit)
