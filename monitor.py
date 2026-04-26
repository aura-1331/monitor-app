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

# ---------- CONFIG ----------
TELEGRAM_TOKEN = "YOUR_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

APP_VERSION = "1.0"
VERSION_URL = "https://api.github.com/repos/aura-1331/monitor-app/releases/latest"

# ---------- BASE ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

ALERT_LOG = os.path.join(BASE_DIR, "alerts.log")
ACTIVITY_LOG = os.path.join(BASE_DIR, "activity.log")

for file in [ALERT_LOG, ACTIVITY_LOG]:
    if not os.path.exists(file):
        with open(file, "w") as f:
            f.write("=== LOG START ===\n")

# ---------- AUTO UPDATE ----------
def check_update():
    try:
        r = requests.get(VERSION_URL, timeout=5).json()

        latest_version = r["tag_name"].replace("v", "")
        download_url = r["assets"][0]["browser_download_url"]

        if latest_version != APP_VERSION:
            print("UPDATE AVAILABLE")

            exe_path = os.path.abspath(sys.argv[0])
            new_exe = exe_path + ".new"

            data = requests.get(download_url, timeout=15).content
            with open(new_exe, "wb") as f:
                f.write(data)

            bat = exe_path + ".bat"

            with open(bat, "w") as f:
                f.write(f"""@echo off
timeout /t 2 >nul
del "{exe_path}"
rename "{new_exe}" "{os.path.basename(exe_path)}"
start "" "{exe_path}"
del "%~f0"
""")

            os.startfile(bat)
            os._exit(0)

    except Exception as e:
        print("UPDATE ERROR:", e)

# ---------- LOG ----------
def log_alert(event, msg, net):
    with open(ALERT_LOG, "a") as f:
        f.write(f"{datetime.now()} | EVENT={event} | MSG={msg} | NET={'ON' if net else 'OFF'}\n")

def log_activity(msg):
    with open(ACTIVITY_LOG, "a") as f:
        f.write(f"{datetime.now()} | {msg}\n")

# ---------- VOICE ----------
def speak(msg, net):
    print("VOICE:", msg)
    log_activity(f"VOICE: {msg}")

    if net:
        try:
            async def run():
                file_path = f"{tempfile.gettempdir()}\\{uuid.uuid4()}.mp3"
                await edge_tts.Communicate(msg, voice="en-US-AriaNeural").save(file_path)
                playsound(file_path)
            asyncio.run(run())
            return
        except Exception as e:
            print("EDGE TTS FAILED:", e)
            log_activity(f"VOICE EDGE FAILED: {e}")

    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 130)
        engine.say(msg)
        engine.runAndWait()
    except Exception as e:
        print("OFFLINE VOICE ERROR:", e)
        log_activity(f"VOICE OFFLINE ERROR: {e}")

# ---------- TELEGRAM ----------
def send_telegram(msg, net):
    if not net:
        print("TELEGRAM SKIPPED")
        log_activity("TELEGRAM SKIPPED")
        return

    def run():
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            res = requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
            print("TELEGRAM:", res.status_code)
            log_activity(f"TELEGRAM SENT: {msg}")
        except Exception as e:
            print("TELEGRAM ERROR:", e)
            log_activity(f"TELEGRAM ERROR: {e}")

    threading.Thread(target=run, daemon=True).start()

# ---------- FLASH ----------
def flash(msg, color="red"):
    def run():
        try:
            root = tk.Tk()
            root.attributes("-fullscreen", True)
            root.configure(bg=color)
            root.attributes("-topmost", True)

            label = tk.Label(root, text=msg,
                             font=("Arial", 40, "bold"),
                             fg="white", bg=color)
            label.pack(expand=True)

            root.after(2000, root.destroy)
            root.mainloop()
        except Exception as e:
            print("FLASH ERROR:", e)
            log_activity(f"FLASH ERROR: {e}")

    threading.Thread(target=run, daemon=True).start()

# ---------- NETWORK ----------
URLS = ["https://www.google.com", "https://www.cloudflare.com"]

def is_connected():
    for url in URLS:
        try:
            if requests.get(url, timeout=3).status_code == 200:
                return True
        except:
            continue
    return False

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

# ---------- MONITOR ----------
def monitor():
    prev_net = None
    fail_count = 0

    while True:
        net = is_connected()

        print(f"NET={net}")
        log_activity(f"NET={net}")

        if prev_net is None:
            prev_net = net

        if net:
            fail_count = 0
        else:
            fail_count += 1

        if prev_net and fail_count >= 1:
            msg = "NETWORK LOST"
            flash(msg)
            speak(msg, net)
            send_telegram(msg, net)
            log_alert("NETWORK", msg, net)
            log_activity(f"ALERT: {msg}")
            prev_net = False

        elif not prev_net and net:
            msg = "NETWORK RESTORED"
            flash(msg, "green")
            speak(msg, net)
            send_telegram(msg, net)
            log_alert("NETWORK", msg, net)
            log_activity(f"ALERT: {msg}")
            prev_net = True

        time.sleep(1)

# ---------- POWER EVENT ----------
def power_event_listener():
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_POWERBROADCAST and wparam == win32con.PBT_APMPOWERSTATUSCHANGE:
            power = get_power_status()
            net = is_connected()

            if power == 0:
                msg_text = "POWER DISCONNECTED"
                flash(msg_text)
                speak(msg_text, net)
                send_telegram(msg_text, net)
                log_alert("POWER", msg_text, net)
                log_activity(f"ALERT: {msg_text}")

            elif power == 1:
                msg_text = "POWER CONNECTED"
                flash(msg_text, "green")
                speak(msg_text, net)
                send_telegram(msg_text, net)
                log_alert("POWER", msg_text, net)
                log_activity(f"ALERT: {msg_text}")

        return 0

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wndproc
    wc.lpszClassName = "PowerMonitor"

    class_atom = win32gui.RegisterClass(wc)
    win32gui.CreateWindow(class_atom, "PowerMonitor", 0, 0, 0, 0, 0, 0, 0, 0, None)
    win32gui.PumpMessages()

# ---------- TRAY ----------
def open_alert_log(icon, item):
    os.startfile(ALERT_LOG)

def open_activity_log(icon, item):
    os.startfile(ACTIVITY_LOG)

def exit_app(icon, item):
    icon.stop()
    os._exit(0)

def tray():
    img = Image.new("RGB", (64, 64), "black")
    d = ImageDraw.Draw(img)
    d.rectangle((16,16,48,48), fill="white")

    menu = pystray.Menu(
        pystray.MenuItem("View Alert Log", open_alert_log),
        pystray.MenuItem("View Activity Log", open_activity_log),
        pystray.MenuItem("Exit", exit_app)
    )

    icon = pystray.Icon("Monitor", img, "System Monitor", menu)

    threading.Thread(target=power_event_listener, daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()

    icon.run()

# ---------- MAIN ----------
if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        check_update()
    tray()