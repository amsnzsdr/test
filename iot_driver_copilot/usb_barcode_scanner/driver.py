import os
import threading
import queue
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import hid
except ImportError:
    import sys
    print("Missing 'hid' library. Please install with 'pip install hidapi'")
    sys.exit(1)

# Environment Variables
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
HID_VENDOR_ID = int(os.environ.get('HID_VENDOR_ID', '0'), 16)
HID_PRODUCT_ID = int(os.environ.get('HID_PRODUCT_ID', '0'), 16)
HID_PATH = os.environ.get('HID_PATH')  # Optional, for composite/multiple devices

# Device Info
DEVICE_INFO = {
    "device_name": "USB Barcode Scanner",
    "device_model": "14880S",
    "manufacturer": "deli",
    "device_type": "Barcode Scanner",
    "connection_protocol": "USB HID (Keyboard Emulation)"
}

# HID Keyboard Scancode Mapping (Minimal, for ASCII)
SCANCODE_MAP = {
    4: 'a', 5: 'b', 6: 'c', 7: 'd', 8: 'e', 9: 'f', 10: 'g', 11: 'h', 12: 'i', 13: 'j', 14: 'k', 15: 'l',
    16: 'm', 17: 'n', 18: 'o', 19: 'p', 20: 'q', 21: 'r', 22: 's', 23: 't', 24: 'u', 25: 'v', 26: 'w', 27: 'x',
    28: 'y', 29: 'z',
    30: '1', 31: '2', 32: '3', 33: '4', 34: '5', 35: '6', 36: '7', 37: '8', 38: '9', 39: '0',
    40: '\n', 44: ' ', 45: '-', 46: '=', 47: '[', 48: ']', 49: '\\', 51: ';', 52: "'", 53: '`', 54: ',', 55: '.', 56: '/',
}
SHIFTED_MAP = {
    4: 'A', 5: 'B', 6: 'C', 7: 'D', 8: 'E', 9: 'F', 10: 'G', 11: 'H', 12: 'I', 13: 'J', 14: 'K', 15: 'L',
    16: 'M', 17: 'N', 18: 'O', 19: 'P', 20: 'Q', 21: 'R', 22: 'S', 23: 'T', 24: 'U', 25: 'V', 26: 'W', 27: 'X',
    28: 'Y', 29: 'Z',
    30: '!', 31: '@', 32: '#', 33: '$', 34: '%', 35: '^', 36: '&', 37: '*', 38: '(', 39: ')',
    40: '\n', 44: ' ', 45: '_', 46: '+', 47: '{', 48: '}', 49: '|', 51: ':', 52: '"', 53: '~', 54: '<', 55: '>', 56: '?',
}

def find_device():
    for d in hid.enumerate():
        if HID_PATH and d['path'] == HID_PATH.encode():
            return d['vendor_id'], d['product_id'], d['path']
        elif HID_VENDOR_ID and HID_PRODUCT_ID:
            if d['vendor_id'] == HID_VENDOR_ID and d['product_id'] == HID_PRODUCT_ID:
                return d['vendor_id'], d['product_id'], d['path']
        elif "14880" in d.get('product_string', '') or "deli" in (d.get('manufacturer_string', '') or '').lower():
            return d['vendor_id'], d['product_id'], d['path']
    return None, None, None

class HIDReaderThread(threading.Thread):
    def __init__(self, barcode_queue):
        super().__init__(daemon=True)
        self.barcode_queue = barcode_queue
        self.last_barcode = ""
        self.keep_running = True

    def run(self):
        while self.keep_running:
            vid, pid, dev_path = find_device()
            if not dev_path:
                time.sleep(1)
                continue
            try:
                h = hid.device()
                if dev_path:
                    h.open_path(dev_path)
                else:
                    h.open(vid, pid)
                h.set_nonblocking(True)
                barcode = ""
                while self.keep_running:
                    data = h.read(8)
                    if not data:
                        time.sleep(0.01)
                        continue
                    modifier = data[0]
                    shift = modifier & 0x22  # left/right shift
                    keycode = data[2]
                    if keycode == 0:
                        continue
                    if keycode in SCANCODE_MAP:
                        char = SHIFTED_MAP[keycode] if shift else SCANCODE_MAP[keycode]
                        if char == '\n':
                            self.last_barcode = barcode
                            self.barcode_queue.put(barcode)
                            barcode = ""
                        else:
                            barcode += char
                h.close()
            except Exception:
                time.sleep(1)

    def get_last_barcode(self):
        try:
            return self.barcode_queue.get_nowait()
        except queue.Empty:
            return self.last_barcode

barcode_queue = queue.Queue()
hid_reader = HIDReaderThread(barcode_queue)
hid_reader.start()

class RequestHandler(BaseHTTPRequestHandler):
    def _set_headers(self, code=200, content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_GET(self):
        if self.path == '/info':
            self._set_headers()
            self.wfile.write(json.dumps(DEVICE_INFO).encode('utf-8'))
        elif self.path == '/scan':
            self._set_headers()
            barcode = hid_reader.get_last_barcode()
            result = {
                "barcode": barcode if barcode else None,
                "timestamp": int(time.time())
            }
            self.wfile.write(json.dumps(result).encode('utf-8'))
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))

    def log_message(self, format, *args):
        return

def run():
    server = HTTPServer((SERVER_HOST, SERVER_PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    run()