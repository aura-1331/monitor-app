import os, time, threading, ctypes, requests, asyncio, tempfile, uuid, sys
from datetime import datetime
import tkinter as tk
import pyttsx3
from PIL import Image, ImageDraw
import pystray
import edge_tts
from playsound import playsound
import win32gui
import win32con
from dotenv import load_dotenv

load_dotenv()

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

APP_VERSION = "1.2.0"
VERSION_URL = "https://api.github.com/repos/aura-1331/monitor-app/releases/latest"

UPDATE_INFO = None

# ---------- UPDATE ----------
def version_tuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except:
        return v

def check_update():
    global UPDATE_INFO
    try:
        r = requests.get(VERSION_URL, timeout=5).json()
        latest = r["tag_name"].replace("v", "").strip()
        current = APP_VERSION.strip()

        if isinstance(version_tuple(latest), tuple) and isinstance(version_tuple(current), tuple):
            if version_tuple(latest) > version_tuple(current):
                print("UPDATE AVAILABLE:", latest)
                UPDATE_INFO = {"version": latest, "url": r["assets"][0]["browser_download_url"]}
            elif version_tuple(latest) == version_tuple(current):
                print("NO UPDATE")
                UPDATE_INFO = None
            else:
                print("LOCAL VERSION NEWER (DEV)")
                UPDATE_INFO = None
        else:
            if latest != current:
                print("UPDATE AVAILABLE (fallback)")
                UPDATE_INFO = {"version": latest, "url": r["assets"][0]["browser_download_url"]}
            else:
                print("NO UPDATE")
                UPDATE_INFO = None

    except Exception as e:
        print("UPDATE ERROR:", e)

def apply_update(icon=None, item=None):
    if not getattr(sys, 'frozen', False):
        print("Update only works in EXE")
        return

    if not UPDATE_INFO:
        print("No update")
        return

    try:
        exe = os.path.abspath(sys.argv[0])
        new = exe + ".new"

        data = requests.get(UPDATE_INFO["url"], timeout=15).content
        with open(new, "wb") as f:
            f.write(data)

        bat = exe + ".bat"
        with open(bat, "w") as f:
            f.write(f"""@echo off
timeout /t 2 >nul
del "{exe}"
rename "{new}" "{os.path.basename(exe)}"
start "" "{exe}"
del "%~f0"
""")

        os.startfile(bat)
        os._exit(0)

    except Exception as e:
        print("UPDATE ERROR:", e)

# ---------- LOG ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

ALERT_LOG = os.path.join(BASE_DIR, "alerts.log")
ACTIVITY_LOG = os.path.join(BASE_DIR, "activity.log")

for f in [ALERT_LOG, ACTIVITY_LOG]:
    if not os.path.exists(f):
        with open(f, "w") as file:
            file.write("=== LOG START ===\n")

def log_alert(event, msg, net):
    with open(ALERT_LOG, "a") as f:
        f.write(f"{datetime.now()} | {event} | {msg} | NET={'ON' if net else 'OFF'}\n")

def log_activity(msg):
    with open(ACTIVITY_LOG, "a") as f:
        f.write(f"{datetime.now()} | {msg}\n")

# ---------- VOICE ----------
def speak(msg, net):
    if net:
        try:
            async def run():
                file = f"{tempfile.gettempdir()}\\{uuid.uuid4()}.mp3"
                await edge_tts.Communicate(msg).save(file)
                playsound(file)
            asyncio.run(run())
            return
        except:
            pass

    try:
        engine = pyttsx3.init()
        engine.say(msg)
        engine.runAndWait()
    except:
        pass

# ---------- TELEGRAM ----------
def send_telegram(msg, net):
    if not net or not TELEGRAM_TOKEN or not CHAT_ID:
        log_activity("TELEGRAM SKIPPED (no internet or config)")
        return

    def run():
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=10)

            if r.status_code == 200:
                log_activity(f"TELEGRAM SENT → {msg}")
            else:
                log_activity(f"TELEGRAM FAILED ({r.status_code})")

        except Exception as e:
            log_activity("TELEGRAM ERROR")

    threading.Thread(target=run, daemon=True).start()

# ---------- FLASH ----------
def flash(msg, color="red"):
    def run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.configure(bg=color)
        root.attributes("-topmost", True)

        tk.Label(root, text=msg, font=("Arial", 40), fg="white", bg=color).pack(expand=True)

        root.after(2000, root.destroy)
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()

# ---------- NETWORK ----------
URLS = ["https://www.google.com", "https://www.cloudflare.com"]

def is_connected():
    for url in URLS:
        try:
            if requests.get(url, timeout=3).status_code == 200:
                return True
        except:
            pass
    return False

# ---------- FIXED MONITOR ----------
def monitor():
    prev = None
    down_since = None

    while True:
        net = is_connected()
        now = time.time()

        print("DEBUG NET:", net)

        if prev is None:
            prev = net

        if not net:
            if down_since is None:
                down_since = now

            if prev and (now - down_since >= 3):
                msg = "NETWORK LOST"
                print(msg)

                flash(msg)
                speak(msg, net)
                send_telegram(msg, net)
                log_alert("NETWORK", msg, net)
                log_activity("NETWORK LOST")

                prev = False

        else:
            if down_since is not None:
                downtime = int(now - down_since)
            else:
                downtime = 0

            if not prev:
                msg = f"NETWORK RESTORED (down {downtime}s)"
                print(msg)

                flash(msg, "green")
                speak(msg, net)
                send_telegram(msg, net)
                log_alert("NETWORK", msg, net)
                log_activity(f"NETWORK RESTORED (down {downtime}s)")

                prev = True

            down_since = None

        time.sleep(1)

# ---------- POWER ----------
from ctypes import Structure, c_byte, c_ulong, POINTER

class SYSTEM_POWER_STATUS(Structure):
    _fields_ = [
        ("ACLineStatus", c_byte),
        ("BatteryFlag", c_byte),
        ("BatteryLifePercent", c_byte),
        ("Reserved1", c_byte),
        ("BatteryLifeTime", c_ulong),
        ("BatteryFullLifeTime", c_ulong),
    ]

GetSystemPowerStatus = ctypes.windll.kernel32.GetSystemPowerStatus
GetSystemPowerStatus.argtypes = [POINTER(SYSTEM_POWER_STATUS)]
GetSystemPowerStatus.restype = ctypes.c_int

def get_power_status():
    status = SYSTEM_POWER_STATUS()
    if not GetSystemPowerStatus(ctypes.pointer(status)):
        return None
    return status.ACLineStatus

def power_event_listener():
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_POWERBROADCAST and wparam == win32con.PBT_APMPOWERSTATUSCHANGE:
            power = get_power_status()
            net = is_connected()

            if power == 0:
                msg = "POWER DISCONNECTED"
                flash(msg)
                speak(msg, net)
                send_telegram(msg, net)
                log_alert("POWER", msg, net)
                log_activity(msg)

            elif power == 1:
                msg = "POWER CONNECTED"
                flash(msg, "green")
                speak(msg, net)
                send_telegram(msg, net)
                log_alert("POWER", msg, net)
                log_activity(msg)

        return 0

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wndproc
    wc.lpszClassName = "PowerMonitor"

    atom = win32gui.RegisterClass(wc)
    win32gui.CreateWindow(atom, "PowerMonitor", 0, 0, 0, 0, 0, 0, 0, 0, None)
    win32gui.PumpMessages()

# ---------- TRAY ----------
def open_alert_log(icon, item):
    os.startfile(ALERT_LOG)

def open_activity_log(icon, item):
    os.startfile(ACTIVITY_LOG)

def tray():
    img = Image.new("RGB", (64, 64), "black")
    d = ImageDraw.Draw(img)
    d.rectangle((16,16,48,48), fill="white")

    menu = pystray.Menu(
    pystray.MenuItem("View Alert Log", open_alert_log),
    pystray.MenuItem("View Activity Log", open_activity_log),

    pystray.MenuItem("Check Updates", lambda: check_update()),
    pystray.MenuItem("Apply Update", apply_update, enabled=lambda item: UPDATE_INFO is not None),

    pystray.MenuItem(f"Version: {APP_VERSION}", lambda: None),
    pystray.MenuItem("Exit", lambda icon, item: os._exit(0))
)

    icon = pystray.Icon("Monitor", img, f"Monitor v{APP_VERSION}", menu)

    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=power_event_listener, daemon=True).start()

    icon.run()

# ---------- MAIN ----------
if __name__ == "__main__":
    check_update()
    tray()