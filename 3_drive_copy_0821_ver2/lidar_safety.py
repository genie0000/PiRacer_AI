"""YDLidar X2 obstacle gate for a DonkeyCar threaded part."""

from __future__ import annotations

import math
import threading
import time


class YDLidarObstaclePart:
    """Stops for a forward obstacle and resumes after consecutive clear scans.

    The X2 binding reports range in metres.  A failed or unavailable LiDAR is
    fail-safe blocked when configured as such.
    """

    def __init__(self, port: str, threshold_m: float = 0.10, forward_half_angle_deg: float = 20,
                 clear_scans_required: int = 3, fail_safe_stop: bool = True) -> None:
        self.port, self.threshold_m = port, threshold_m
        self.forward_half_angle_rad = math.radians(forward_half_angle_deg)
        self.clear_scans_required, self.fail_safe_stop = clear_scans_required, fail_safe_stop
        self._lock = threading.Lock()
        self._blocked = fail_safe_stop
        self._connected = False
        self._nearest_m: float | None = None
        self._clear_scans = 0
        self._laser = None

    def update(self) -> None:
        """DonkeyCar threaded loop: initialize and continuously process scans."""
        try:
            import ydlidar
            ydlidar.os_init()
            laser = ydlidar.CYdLidar()
            laser.setlidaropt(ydlidar.LidarPropSerialPort, self.port)
            laser.setlidaropt(ydlidar.LidarPropSerialBaudrate, 115200)
            laser.setlidaropt(ydlidar.LidarPropLidarType, ydlidar.TYPE_TRIANGLE)
            laser.setlidaropt(ydlidar.LidarPropDeviceType, ydlidar.YDLIDAR_TYPE_SERIAL)
            laser.setlidaropt(ydlidar.LidarPropSampleRate, 3)
            laser.setlidaropt(ydlidar.LidarPropScanFrequency, 6.0)
            laser.setlidaropt(ydlidar.LidarPropSingleChannel, True)
            laser.setlidaropt(ydlidar.LidarPropAutoReconnect, True)
            laser.setlidaropt(ydlidar.LidarPropMinRange, 0.02)
            laser.setlidaropt(ydlidar.LidarPropMaxRange, 8.0)
            if not laser.initialize() or not laser.turnOn():
                raise RuntimeError("YDLidar X2 initialize/turnOn failed")
            self._laser = laser
            with self._lock:
                self._connected = True
            scan = ydlidar.LaserScan()
            while True:
                if laser.doProcessSimple(scan):
                    self._accept_scan(scan.points)
                else:
                    self._mark_failed()
                    time.sleep(0.05)
        except Exception as exc:
            print(f"LiDAR safety unavailable: {exc}")
            self._mark_failed()

    def run_threaded(self) -> tuple[bool, bool, float | None]:
        with self._lock:
            return self._blocked, self._connected, self._nearest_m

    def shutdown(self) -> None:
        if self._laser is not None:
            self._laser.turnOff()
            self._laser.disconnecting()

    def _accept_scan(self, points) -> None:
        nearest = None
        for point in points:
            distance = float(point.range)
            angle = float(point.angle)
            # X2 angles are radians; 0 radians is the configured forward axis.
            normalized = math.atan2(math.sin(angle), math.cos(angle))
            if distance > 0 and abs(normalized) <= self.forward_half_angle_rad:
                nearest = distance if nearest is None else min(nearest, distance)
        obstacle = nearest is not None and nearest <= self.threshold_m
        with self._lock:
            self._connected, self._nearest_m = True, nearest
            if obstacle:
                self._blocked, self._clear_scans = True, 0
            else:
                self._clear_scans += 1
                if self._clear_scans >= self.clear_scans_required:
                    self._blocked = False

    def _mark_failed(self) -> None:
        with self._lock:
            self._connected, self._nearest_m, self._clear_scans = False, None, 0
            if self.fail_safe_stop:
                self._blocked = True
