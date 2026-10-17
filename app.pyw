"""EspansoUI: واجهة بسيطة لإضافة اختصارات Espanso دون تعديل ملف YAML يدويًا.

يشغّل خادمًا محليًا صغيرًا ويفتح الواجهة في نافذة Edge مستقلة (وضع التطبيق).
لا يحتاج أي حزمة خارجية، فقط Python.

© 2026 Ali Alhumedi - MIT License
"""

import ctypes
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espanso_file  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = secrets.token_hex(16)
last_ping = time.time()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _lang(self):
        return "en" if self.headers.get("X-Lang") == "en" else "ar"

    def _allowed(self):
        host = self.headers.get("Host", "")
        return host.startswith("127.0.0.1:") and self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        global last_ping
        if self.path == "/":
            with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
                html = f.read().replace("__TOKEN__", TOKEN)
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path == "/api/matches" and self._allowed():
            try:
                self._json(200, {"ok": True, "matches": espanso_file.load(),
                                 "file": espanso_file.MATCH_FILE})
            except espanso_file.UnsupportedFile as e:
                self._json(200, {"ok": False, "readonly": True, "error": e.text(self._lang()),
                                 "file": espanso_file.MATCH_FILE})
        elif self.path == "/api/ping" and self._allowed():
            last_ping = time.time()
            self._json(200, {"ok": True})
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/save" or not self._allowed():
            return self._send(403, "forbidden", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            saved = espanso_file.save(data["matches"])
            self._json(200, {"ok": True, "matches": saved})
        except espanso_file.AppError as e:
            self._json(200, {"ok": False, "error": e.text(self._lang())})
        except Exception as e:  # أي خطأ آخر يُعرض للمستخدم ولا يوقف البرنامج
            prefix = "Unexpected error" if self._lang() == "en" else "خطأ غير متوقع"
            self._json(200, {"ok": False, "error": f"{prefix}: {e}"})


def find_edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(p):
                return p
    return None


# عنوان النافذة بالعربية أو الإنجليزية حسب لغة الواجهة
WINDOW_TITLES = ("اختصاراتي", "My Shortcuts")


def find_window():
    """يبحث عن نافذة الواجهة المفتوحة ويرجع رقمها، أو None."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def check(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            # Windows قد يضيف علامات اتجاه حول العنوان العربي
            if buf.value.strip("\u202a\u202b\u202c\u200e\u200f") in WINDOW_TITLES:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(check, 0)
    return found[0] if found else None


def bring_existing_to_front():
    """إن كانت الواجهة مفتوحة نُظهرها بدل فتح نافذة ثانية."""
    user32 = ctypes.windll.user32
    for _ in range(40):  # قد تكون النسخة الأولى ما زالت تفتح نافذتها
        hwnd = find_window()
        if hwnd:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
        time.sleep(0.25)


def main():
    # قفل باسم ثابت: تعمل نسخة واحدة فقط من البرنامج
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel32.CreateMutexW(None, False, "Local\\EspansoUI-single")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        bring_existing_to_front()
        return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    edge = find_edge()
    if edge:
        profile = os.path.join(espanso_file.LOCALAPPDATA, "EspansoUI", "window")
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={profile}",
                          "--window-size=820,760", "--no-first-run",
                          "--no-default-browser-check"])
    else:
        import webbrowser
        webbrowser.open(url)

    # الواجهة ترسل نبضة كل ثانيتين، فإذا أُغلقت النافذة يتوقف البرنامج وحده
    global last_ping
    last_ping = time.time() + 20  # مهلة لفتح النافذة أول مرة
    while time.time() - last_ping < 8:
        time.sleep(1)
    server.shutdown()


if __name__ == "__main__":
    main()
