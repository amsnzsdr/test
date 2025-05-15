import os
import sys
import threading
import time
import http.server
import json

# Configuration from environment variables
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
HID_DEVICE_PATH = os.environ.get("HID_DEVICE_PATH", "/dev/hidraw0")
BARCODE_MAX_LENGTH = int(os.environ.get("BARCODE_MAX_LENGTH", "128"))

DEVICE_INFO = {
    "device_name": "USB Barcode Scanner",
    "device_model": "14880S",
    "manufacturer": "deli",
    "device_type": "Barcode Scanner",
    "connection_protocol": "USB HID (Keyboard Emulation)"
}

class BarcodeScanner:
    def __init__(self, hid_device_path, barcode_max_length):
        self.hid_device_path = hid_device_path
        self.barcode_max_length = barcode_max_length
        self.last_barcode = ""
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self.thread = threading.Thread(target=self._read_hid_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop_event.set()
        self.thread.join()

    def get_last_barcode(self):
        with self.lock:
            return self.last_barcode

    # HID keycode to ASCII (US keyboard, HID report format: [modifier, 0, key1, key2, ...])
    # Only maps 0-9, a-z, A-Z, and Enter (as scan end). Extend as needed.
    KEYCODE_MAP = {
        0x04: 'a', 0x05: 'b', 0x06: 'c', 0x07: 'd', 0x08: 'e', 0x09: 'f', 0x0a: 'g', 0x0b: 'h', 0x0c: 'i', 0x0d: 'j',
        0x0e: 'k', 0x0f: 'l', 0x10: 'm', 0x11: 'n', 0x12: 'o', 0x13: 'p', 0x14: 'q', 0x15: 'r', 0x16: 's', 0x17: 't',
        0x18: 'u', 0x19: 'v', 0x1a: 'w', 0x1b: 'x', 0x1c: 'y', 0x1d: 'z',
        0x1e: '1', 0x1f: '2', 0x20: '3', 0x21: '4', 0x22: '5', 0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
        0x28: '\n',
    }
    SHIFT_MAP = {
        0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F', 0x0a: 'G', 0x0b: 'H', 0x0c: 'I', 0x0d: 'J',
        0x0e: 'K', 0x0f: 'L', 0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P', 0x14: 'Q', 0x15: 'R', 0x16: 'S', 0x17: 'T',
        0x18: 'U', 0x19: 'V', 0x1a: 'W', 0x1b: 'X', 0x1c: 'Y', 0x1d: 'Z',
        0x1e: '!', 0x1f: '@', 0x20: '#', 0x21: '$', 0x22: '%', 0x23: '^', 0x24: '&', 0x25: '*', 0x26: '(', 0x27: ')',
        0x28: '\n',
    }

    def _parse_hid_report(self, report):
        """Parse HID report bytes to a string character (if any)"""
        if len(report) < 3:
            return None
        modifier = report[0]
        shift = modifier & 0x22  # left or right shift
        keycode = report[2]
        if keycode == 0:
            return None
        if shift:
            return self.SHIFT_MAP.get(keycode)
        else:
            return self.KEYCODE_MAP.get(keycode)

    def _read_hid_loop(self):
        barcode_chars = []
        try:
            with open(self.hid_device_path, "rb") as f:
                while not self._stop_event.is_set():
                    report = f.read(8)
                    if len(report) < 8:
                        continue
                    c = self._parse_hid_report(report)
                    if c is None:
                        continue
                    if c == '\n':
                        barcode = ''.join(barcode_chars)
                        if barcode:
                            with self.lock:
                                self.last_barcode = barcode
                        barcode_chars.clear()
                    else:
                        if len(barcode_chars) < self.barcode_max_length:
                            barcode_chars.append(c)
        except Exception as e:
            # Device not present or error; clears last barcode and retries periodically
            with self.lock:
                self.last_barcode = ""
            while not self._stop_event.is_set():
                time.sleep(2)
                try:
                    with open(self.hid_device_path, "rb") as f:
                        # If we can open, restart reading
                        self._read_hid_loop()
                except Exception:
                    pass

scanner = BarcodeScanner(HID_DEVICE_PATH, BARCODE_MAX_LENGTH)

class BarcodeHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/info":
            self._json_response(200, DEVICE_INFO)
        elif self.path == "/scan":
            barcode = scanner.get_last_barcode()
            resp = {"barcode": barcode}
            self._json_response(200, resp)
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"Not found"}')

    def log_message(self, format, *args):
        return  # Suppress logging to stderr

    def _json_response(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def run_server():
    server = http.server.ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), BarcodeHTTPRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scanner.stop()
        server.server_close()

if __name__ == "__main__":
    run_server()
