import os
import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import requests
import socket
import select

# Environment Variables
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASS = os.environ.get("CAMERA_PASS", "12345")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))
RTSP_SERVER_PORT = int(os.environ.get("RTSP_SERVER_PORT", "40554"))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", CAMERA_USER)
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", CAMERA_PASS)
RTSP_STREAM_PATH = os.environ.get("RTSP_STREAM_PATH", "Streaming/Channels/101")

SNAPSHOT_PATH = f"http://{CAMERA_IP}/ISAPI/Streaming/channels/101/picture"

def build_rtsp_url():
    return f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{CAMERA_IP}:{RTSP_SERVER_PORT}/{RTSP_STREAM_PATH}"

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

class CameraRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/snap":
            self.handle_snapshot()
        elif self.path == "/live":
            self.handle_live_stream()
        else:
            self.send_error(404, "Not Found")

    def handle_snapshot(self):
        try:
            resp = requests.get(
                SNAPSHOT_PATH,
                auth=(CAMERA_USER, CAMERA_PASS),
                timeout=10,
                stream=True
            )
            if resp.status_code == 200 and resp.headers.get("Content-Type", "").lower().startswith("image/jpeg"):
                self.send_response(200)
                self.send_header("Content-type", "image/jpeg")
                self.end_headers()
                for chunk in resp.iter_content(chunk_size=8192):
                    self.wfile.write(chunk)
            else:
                self.send_error(502, "Failed to fetch snapshot from camera")
        except Exception as e:
            self.send_error(502, f"Snapshot error: {str(e)}")

    def handle_live_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")  # For browser compatibility; actual stream is proxied
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with socket.create_connection((CAMERA_IP, RTSP_SERVER_PORT), timeout=10) as upstream:
                # Start RTSP handshake with the camera
                rtsp_url = build_rtsp_url()
                client = RTSPClient(upstream, self.wfile, rtsp_url)
                client.start_stream()
        except Exception as e:
            self.log_error("RTSP proxy error: %s", str(e))
            try:
                self.wfile.write(b"RTSP proxy error\n")
            except:
                pass

    def log_message(self, format, *args):
        pass  # Silence default logging

class RTSPClient:
    def __init__(self, upstream_sock, out_stream, rtsp_url):
        self.upstream_sock = upstream_sock
        self.out_stream = out_stream
        self.rtsp_url = rtsp_url
        self.cseq = 1
        self.session = None
        self.transport = None

    def send_rtsp(self, cmd, extra_headers=None, body=None):
        headers = [
            f"{cmd} RTSP/1.0",
            f"CSeq: {self.cseq}",
        ]
        if self.session:
            headers.append(f"Session: {self.session}")
        if extra_headers:
            headers.extend(extra_headers)
        headers.append("")
        if body:
            headers.append(body)
        else:
            headers.append("")
        content = "\r\n".join(headers)
        self.upstream_sock.sendall(content.encode("utf-8"))
        self.cseq += 1

    def recv_rtsp(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.upstream_sock.recv(4096)
            if not chunk:
                raise Exception("RTSP server closed connection")
            buf += chunk
        header, rest = buf.split(b"\r\n\r\n", 1)
        lines = header.decode().split("\r\n")
        status_line = lines[0]
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        return status_line, headers, rest

    def start_stream(self):
        # 1. OPTIONS
        self.send_rtsp(f"OPTIONS {self.rtsp_url}")
        self.recv_rtsp()
        # 2. DESCRIBE
        self.send_rtsp(f"DESCRIBE {self.rtsp_url}", ["Accept: application/sdp"])
        _, describe_headers, describe_rest = self.recv_rtsp()
        # 3. SETUP (interleaved over TCP)
        track_id = self._find_track_id(describe_rest)
        self.send_rtsp(
            f"SETUP {self.rtsp_url}/{track_id}",
            [f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1"]
        )
        _, setup_headers, _ = self.recv_rtsp()
        self.session = setup_headers.get("Session", "").split(";")[0]
        # 4. PLAY
        self.send_rtsp(f"PLAY {self.rtsp_url}/{track_id}", [])
        self.recv_rtsp()
        # 5. Proxy RTP over HTTP response
        self.proxy_rtp_stream()

    def _find_track_id(self, sdp_blob):
        # Naive SDP parsing for trackID or trackID=1
        sdp = sdp_blob.decode(errors="ignore")
        for line in sdp.splitlines():
            if line.startswith("a=control:"):
                val = line[len("a=control:") :]
                if val.startswith("rtsp://") or val.startswith("/"):
                    return val.split("/")[-1]
                else:
                    return val
        return "trackID=1"

    def proxy_rtp_stream(self):
        self.upstream_sock.setblocking(False)
        while True:
            rlist, _, _ = select.select([self.upstream_sock], [], [], 10)
            if self.upstream_sock in rlist:
                try:
                    data = self.upstream_sock.recv(4096)
                    if not data:
                        break
                    # RTP over RTSP TCP framing: $ <channel> <len-hi> <len-lo> <payload>
                    # Proxy only RTP payload, skip the RTSP framing bytes
                    ptr = 0
                    while ptr < len(data):
                        if data[ptr] == 0x24:  # $
                            if ptr + 4 > len(data):
                                break
                            channel = data[ptr + 1]
                            size = (data[ptr + 2] << 8) | data[ptr + 3]
                            if ptr + 4 + size > len(data):
                                break
                            payload = data[ptr + 4 : ptr + 4 + size]
                            self.out_stream.write(payload)
                            self.out_stream.flush()
                            ptr += 4 + size
                        else:
                            ptr += 1
                except Exception:
                    break

def run_server():
    server = ThreadedHTTPServer((HTTP_SERVER_HOST, HTTP_SERVER_PORT), CameraRequestHandler)
    print(f"Camera driver HTTP server running at http://{HTTP_SERVER_HOST}:{HTTP_SERVER_PORT}")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
