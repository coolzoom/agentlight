import os
import time

import serial
from serial.tools import list_ports

DEFAULT_DEVICE_ID = "agent-signal-light-v1"
DEFAULT_BAUD_RATE = 115200
IDENTIFY_COMMAND = b"identify\n"
PROBE_TIMEOUT = 0.6


def score_port(port):
    text = " ".join(
        [
            port.device or "",
            port.description or "",
            port.manufacturer or "",
            port.hwid or "",
        ]
    ).lower()

    score = 0
    if "esp32" in text:
        score += 100
    if "usb" in text:
        score += 20
    if "jtag" in text or "serial" in text:
        score += 10
    if "bluetooth" in text or "bth" in text:
        score -= 100
    return score


def expected_device_id():
    return os.environ.get("AGENT_LIGHT_DEVICE_ID", DEFAULT_DEVICE_ID).strip() or DEFAULT_DEVICE_ID


def legacy_detect_enabled():
    return os.environ.get("AGENT_LIGHT_LEGACY_DETECT", "").strip().lower() in {"1", "true", "yes"}


def probe_port(device, baud_rate=DEFAULT_BAUD_RATE, device_id=None):
    device_id = device_id or expected_device_id()
    ser = None
    try:
        ser = serial.Serial()
        ser.port = device
        ser.baudrate = baud_rate
        ser.timeout = 0.2
        ser.write_timeout = 0.5
        ser.dtr = False
        ser.rts = False
        ser.open()
        time.sleep(0.25)
        if ser.in_waiting:
            ser.read(ser.in_waiting)
        ser.write(IDENTIFY_COMMAND)
        ser.flush()

        buffer = ""
        deadline = time.monotonic() + PROBE_TIMEOUT
        while time.monotonic() < deadline:
            waiting = ser.in_waiting
            if waiting:
                buffer += ser.read(waiting).decode("utf-8", errors="ignore")
                if device_id in buffer:
                    return True, device_id
            else:
                time.sleep(0.05)
        return device_id in buffer, buffer.strip()
    except Exception as exc:
        return False, str(exc)
    finally:
        if ser and ser.is_open:
            ser.close()


def detect_port(baud_rate=DEFAULT_BAUD_RATE, device_id=None):
    device_id = device_id or expected_device_id()
    ports = list(list_ports.comports())
    if not ports:
        return None

    ranked = sorted(ports, key=score_port, reverse=True)
    for port in ranked:
        matched, _detail = probe_port(port.device, baud_rate, device_id)
        if matched:
            return port.device

    if legacy_detect_enabled() and ranked:
        return ranked[0].device
    return None


def open_serial(port, baud_rate=DEFAULT_BAUD_RATE, settle_seconds=1.5):
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud_rate
    ser.timeout = 0.3
    ser.write_timeout = 1
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(settle_seconds)
    try:
        if ser.in_waiting:
            ser.read(ser.in_waiting)
    except serial.SerialException:
        pass
    return ser
