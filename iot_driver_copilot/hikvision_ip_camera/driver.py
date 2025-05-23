import os
import io
import threading
import requests
import http.server
import socketserver
from http import HTTPStatus
import base64
import socket

from urllib.parse import urlparse

from queue import Queue

import time

import cv2
import numpy as np

# ========== Environment Variables ==========

CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))
CAMERA_HTTP_PORT = int(os.environ.get("CAMERA_HTTP_PORT", "80"))
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "40554"))
RTSP_PATH = os.environ.get("CAMERA_RTSP_PATH", "/Streaming/Channels/101")
# Acceptable values for RTSP_PATH: e.g. /Streaming/Channels/101

# ========== Helper Functions ==========

def get_snapshot():
    url = f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/ISAPI/Streaming/channels/101/picture"
    auth = (CAMERA_USER, CAMERA_PASSWORD)
    try:
        response = requests.get(url, auth=auth, timeout=5)
        if response.status_code == 200 and response.headers.get('Content-Type', '').startswith('image/'):
            return response.content
        else:
            # Fallback: try another known snapshot endpoint
            url2 = f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/Streaming/channels/101/picture"
            response2 = requests.get(url2, auth=auth, timeout=5)
            if response2.status_code == 200 and response2.headers.get('Content-Type', '').startswith('image/'):
                return response2.content
            else:
                return None
    except requests.RequestException:
        return None

def gen_mjpeg_stream(rtsp_url, stop_event):
    # Use OpenCV to open the RTSP stream and yield JPEG frames in multipart/x-mixed-replace format.
    # This allows direct browser viewing.
    cap = cv2.VideoCapture(rtsp_url)
    try:
        if not cap.isOpened():
            yield None
            return
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            ret2, jpeg = cv2.imencode('.jpg', frame)
            if not ret2:
                continue
            img_bytes = jpeg.tobytes()
            yield img_bytes
            time.sleep(0.04)  # ~25 FPS
    finally:
        cap.release()

def get_rtsp_url():
    user = CAMERA_USER
    password = CAMERA_PASSWORD
    ip = CAMERA_IP
    port = CAMERA_RTSP_PORT
    path = RTSP_PATH
    if user and password:
        return f"rtsp://{user}:{password}@{ip}:{port}{path}"
    else:
        return f"rtsp://{ip}:{port}{path}"

# ========== HTTP Handler ==========

class CameraRequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "HikvisionProxy/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/snap":
            self.handle_snap()
        elif self.path == "/live":
            self.handle_live()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def handle_snap(self):
        img_data = get_snapshot()
        if img_data is None:
            self.send_error(HTTPStatus.BAD_GATEWAY, "Failed to retrieve snapshot from camera")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(img_data)))
        self.end_headers()
        self.wfile.write(img_data)

    def handle_live(self):
        # Serve MJPEG stream converted from RTSP
        rtsp_url = get_rtsp_url()
        stop_event = threading.Event()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            for frame in gen_mjpeg_stream(rtsp_url, stop_event):
                if frame is None:
                    break
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode('utf-8'))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except Exception:
            pass
        finally:
            stop_event.set()

    def log_message(self, format, *args):
        return

# ========== Server ==========

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def run():
    server_address = (HTTP_SERVER_HOST, HTTP_SERVER_PORT)
    httpd = ThreadingHTTPServer(server_address, CameraRequestHandler)
    print(f"Serving on {HTTP_SERVER_HOST}:{HTTP_SERVER_PORT} (snap: /snap, live: /live)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()

if __name__ == "__main__":
    run()
