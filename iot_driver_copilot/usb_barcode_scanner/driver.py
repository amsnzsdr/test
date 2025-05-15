import os
import threading
import queue
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import hid
except ImportError:
    hid = None

DEVICE_INFO = {
    "device_name": "USB Barcode Scanner",
    "device_model": "14880S",
    "manufacturer": "deli",
    "device_type": "Barcode Scanner",
    "connection_protocol": "USB HID (Keyboard Emulation)"
}

HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
HID_VENDOR_ID = int(os.environ.get("HID_VENDOR_ID", "0x0c2e"), 16)
HID_PRODUCT_ID = int(os.environ.get("HID_PRODUCT_ID", "0x1488"), 16)

SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "10"))

scans_queue = queue.Queue(maxsize=10)
last_scan_time = 0
last_scan = ""

KEYMAP = {
    4: 'a', 5: 'b', 6: 'c', 7: 'd', 8: 'e', 9: 'f', 10: 'g', 11: 'h', 12: 'i', 13: 'j',
    14: 'k', 15: 'l', 16: 'm', 17: 'n', 18: 'o', 19: 'p', 20: 'q', 21: 'r', 22: 's', 23: 't',
    24: 'u', 25: 'v', 26: 'w', 27: 'x', 28: 'y', 29: 'z',
    30: '1', 31: '2', 32: '3', 33: '4', 34: '5', 35: '6', 36: '7', 37: '8', 38: '9', 39: '0',
    40: '\n', 44: ' ', 45: '-', 46: '=', 47: '[', 48: ']', 49: '\\', 51: ';', 52: "'", 53: '`',
    54: ',', 55: '.', 56: '/',
    # Shifted chars
    (30, True): '!', (31, True): '@', (32, True): '#', (33, True): '$', (34, True): '%', (35, True): '^',
    (36, True): '&', (37, True): '*', (38, True): '(', (39, True): ')',
    (45, True): '_', (46, True): '+', (47, True): '{', (48, True): '}', (49, True): '|',
    (51, True): ':', (52, True): '"', (53, True): '~', (54, True): '<', (55, True): '>', (56, True): '?'
}

def decode_keys(data):
    """Decodes HID report bytes to a string."""
    result = ""
    shift = (data[0] & 0x22) != 0
    for code in data[2:]:
        if code == 0:
            continue
        if code == 40:
            result += '\n'
            continue
        if shift and (code, True) in KEYMAP:
            result += KEYMAP[(code, True)]
        elif code in KEYMAP:
            val = KEYMAP[code]
            if shift and 'a' <= val <= 'z':
                val = val.upper()
            result += val
    return result

def hid_reader():
    global last_scan_time, last_scan
    if hid is None:
        return
    while True:
        try:
            h = hid.device()
            h.open(HID_VENDOR_ID, HID_PRODUCT_ID)
            h.set_nonblocking(True)
            buffer = ""
            while True:
                data = h.read(8, timeout=100)
                if data:
                    chars = decode_keys(data)
                    if chars:
                        buffer += chars
                        if '\n' in chars:
                            scan_val = buffer.strip()
                            if scan_val:
                                last_scan = scan_val
                                last_scan_time = time.time()
                                try:
                                    scans_queue.put(scan_val, timeout=0.2)
                                except queue.Full:
                                    pass
                            buffer = ""
                time.sleep(0.01)
        except Exception:
            time.sleep(2)

class DriverHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/info":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(DEVICE_INFO).encode("utf-8"))
        elif self.path == "/scan":
            scan = None
            try:
                scan = scans_queue.get(timeout=SCAN_TIMEOUT)
            except queue.Empty:
                # fallback: last scan if recent
                if last_scan and time.time() - last_scan_time < SCAN_TIMEOUT:
                    scan = last_scan
            if scan:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(scan.encode("utf-8"))
            else:
                self.send_response(204)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def main():
    if hid:
        t = threading.Thread(target=hid_reader, daemon=True)
        t.start()
    server = HTTPServer((HTTP_HOST, HTTP_PORT), DriverHandler)
    server.serve_forever()

if __name__ == "__main__":
    main()
