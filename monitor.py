# AURA MONITOR v1.1.0 — 100% COMPLETE UNABRIDGED VERSION

import sys, os, asyncio, psutil, socket, datetime, threading, subprocess, pygame, json, winreg, time, requests
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from telegram import Bot
import edge_tts
from pystray import Icon, MenuItem, Menu
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
APP_VERSION = "1.2.0"

active_overlay = None

class VoiceManager:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.piper_exe = os.path.join(self.base_dir, "piper", "piper.exe")
        self.model = os.path.join(self.base_dir, "piper", "en_US-lessac-medium.onnx")
        try:
            pygame.mixer.init()
        except:
            pass
        self.volume = 1.0
        self.cleanup_temp_files()

    def set_volume(self, vol):
        self.volume = vol
        if pygame.mixer.get_init():
            pygame.mixer.music.set_volume(vol)

    def cleanup_temp_files(self):
        """Removes all temporary voice files to prevent storage bloat."""
        try:
            for f in os.listdir(self.base_dir):
                if f.startswith("v_") and (f.endswith(".mp3") or f.endswith(".wav")):
                    try:
                        os.remove(os.path.join(self.base_dir, f))
                    except:
                        pass
        except:
            pass

    def stop_audio(self):
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            try:
                pygame.mixer.music.unload()
            except:
                pass
        # Kill piper to release the file lock immediately
        subprocess.run('taskkill /f /im piper.exe', shell=True, capture_output=True)

    async def speak(self, text, is_online, muted):
        if muted: return
        self.stop_audio()
        self.cleanup_temp_files()
        await asyncio.sleep(0.2)
        
        ts = int(time.time())
        # We start by assuming we might use online if is_online is True
        use_online = is_online
        filename = os.path.join(self.base_dir, f"v_{ts}")

        try:
            if use_online:
                try:
                    # RACES Edge-TTS: If it takes > 3 seconds, it triggers the 'except'
                    comm = edge_tts.Communicate(text, "en-US-AndrewNeural")
                    await asyncio.wait_for(comm.save(filename + ".mp3"), timeout=3.0)
                    final_file = filename + ".mp3"
                except (asyncio.TimeoutError, Exception):
                    # Internet is too slow! Force Piper immediately
                    use_online = False

            if not use_online:
                final_file = filename + ".wav"
                cmd = f'echo {text} | "{self.piper_exe}" --model "{self.model}" --output_file "{final_file}"'
                subprocess.check_call(cmd, shell=True)

            # Standard playback logic
            for _ in range(30):
                if os.path.exists(final_file) and os.path.getsize(final_file) > 0: break
                await asyncio.sleep(0.1)

            if os.path.exists(final_file):
                pygame.mixer.music.load(final_file)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
        except Exception as e:
            print(f"Voice Error: {e}")

class AdvancedLogger:
    def __init__(self, activity_file="activity.log", alert_file="alert.log"):
        self.activity_file = activity_file
        self.alert_file = alert_file

    def log(self, message, is_alert=False, max_mb=5):
        now = datetime.datetime.now()
        date_header = now.strftime("%d-%m-%Y %A ---->")
        
        tags = {
            "POWER LOST": "[⚡-]", 
            "POWER RESTORED": "[⚡+]", 
            "NETWORK LOST": "[🌐-]", 
            "NETWORK RESTORED": "[🌐+]"
        }
        tag = next((v for k, v in tags.items() if k in message), "[ACTION]")
        
        full_msg = f"{now.strftime('%H:%M:%S')} {tag} {message}"
        
        # Determine which files to write to
        targets = [self.activity_file]
        if is_alert:
            targets.append(self.alert_file)
            
        for f_path in targets:
            # Check for file size and reset if needed
            if os.path.exists(f_path) and os.path.getsize(f_path) > (max_mb * 1024 * 1024):
                with open(f_path, 'w', encoding='utf-8') as f:
                    f.write(f"--- LOG RESET: Size limit reached on {now} ---\n")
            
            # Read content to check if we need a new date header
            content = ""
            if os.path.exists(f_path):
                with open(f_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            
            if date_header not in content:
                with open(f_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n\n{'#'*50}\n{date_header}\n{'#'*50}\n")
            
            with open(f_path, 'a', encoding='utf-8') as f:
                f.write(f"{full_msg}\n")

class FlashOverlay(QWidget):
    def __init__(self, color, message):
        global active_overlay
        if active_overlay:
            active_overlay.close()
        super().__init__()
        active_overlay = self
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet(f"background-color: {color};")
        layout = QVBoxLayout()
        self.label = QLabel(message)
        self.label.setStyleSheet("color: white; font-size: 45pt; font-weight: bold; padding: 40px;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.setLayout(layout)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.toggle_opacity)
        self.timer.start(200)
        QTimer.singleShot(6000, self.close)

    def toggle_opacity(self):
        self.setWindowOpacity(0.3 if self.windowOpacity() > 0.5 else 1.0)

class MonitorThread(QThread):
    alert_signal = pyqtSignal(str, str, str)
    
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.last_ssid = ""

    def get_ssid(self):
        """Uses PowerShell to get the actual network name, bypassing netsh errors."""
        try:
            # This command is much more reliable on modern Windows
            cmd = ['powershell.exe', '(get-netconnectionProfile).Name']
            ssid = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            
            # If PowerShell returns a list of names, just take the first one
            if ssid:
                return ssid.split('\n')[0].strip()
        except Exception as e:
            # Fallback to a simpler netsh check if PowerShell fails
            try:
                res = subprocess.check_output('netsh wlan show interfaces', shell=True).decode('utf-8', errors='ignore')
                for line in res.split('\n'):
                    if " SSID" in line and " BSSID" not in line:
                        return line.split(":")[1].strip()
            except:
                pass
                
        return "Unknown Network"

    def is_connected(self):
        # 4-LAYER NETWORK CHECK
        # Layer 1: OS/HTTP
        try:
            import urllib.request
            urllib.request.urlopen('http://www.google.com', timeout=3)
            return True
        except: pass
        # Layer 2: DNS
        try:
            socket.gethostbyname("google.com")
            return True
        except: pass
        # Layer 3: Socket
        for target in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
            try:
                socket.create_connection(target, timeout=3)
                return True
            except: continue
        # Layer 4: Ping
        try:
            res = subprocess.run("ping -n 1 -w 1000 8.8.8.8", shell=True, capture_output=True)
            if res.returncode == 0: return True
        except: pass
        return False

    def run(self):
        # 1. Initialize 'last' states so we have something to compare to
        battery = psutil.sensors_battery()
        lp = battery.power_plugged if battery else None
        ln = self.is_connected()
        self.last_ssid = self.get_ssid() if ln else "None"
        
        p_time = n_time = time.time()

        while True:
            battery = psutil.sensors_battery()
            p, percent = (battery.power_plugged, battery.percent) if battery else (None, 0)
            
            # 1. Check Hardware SSID first (FAST)
            s = self.get_ssid()
            
            # 2. Check Internet Ping (SLOW on bad networks)
            n = self.is_connected()

            # NETWORK CHANGE DETECTION
            if n != ln:
                if not n:
                    time.sleep(4)
                    n = self.is_connected()

                if n != ln:
                    dur = str(datetime.timedelta(seconds=int(time.time() - n_time)))
                    stat = "RESTORED" if n else "LOST"
                    
                    # We use 's' (SSID) even if the ping 'n' is still sluggish
                    msg = f"NETWORK {stat}"
                    if n: 
                        msg += f" on {s} - Downtime: {dur}"
                    
                    self.alert_signal.emit("N", msg, "blue" if n else "yellow")
                    ln, n_time = n, time.time()
                    self.last_ssid = s

            # SSID CHANGE DETECTION (Announce if we swap Wi-Fi while online)
            elif n and s != self.last_ssid and s != "Unknown Network":
                msg = f"NETWORK SWITCHED to {s}"
                self.alert_signal.emit("N", msg, "blue")
                self.last_ssid = s

            # POWER CHANGE DETECTION
            if p is not None and p != lp:
                dur = str(datetime.timedelta(seconds=int(time.time() - p_time)))
                stat = "RESTORED" if p else "LOST"
                msg = f"POWER {stat} ({percent}%)" + (f" - Downtime: {dur}" if p else "")
                self.alert_signal.emit("P", msg, "green" if p else "red")
                lp, p_time = p, time.time()

            self.msleep(2000)

def set_startup(enabled):
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    exe = f'"{os.path.realpath(sys.argv[0])}"'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, "AuraMonitor", 0, winreg.REG_SZ, exe)
        else:
            winreg.DeleteValue(key, "AuraMonitor")
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Startup Error: {e}")

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    logger = AdvancedLogger()
    voice = VoiceManager()
    msg_queue = []
    
    settings = {
        "volume": 1.0, 
        "repeat_interval": 300, 
        "reminders_on": True, 
        "startup": False, 
        "silent_mode": False, 
        "max_log_mb": 5, 
        "check_on_start": True
    }
    
    if os.path.exists("settings.json"):
        with open("settings.json", "r") as f:
            settings.update(json.load(f))
    
    voice.set_volume(settings["volume"])
    
    loop = asyncio.new_event_loop()
    def run_async_loop(l):
        asyncio.set_event_loop(l)
        l.run_forever()
    
    threading.Thread(target=run_async_loop, args=(loop,), daemon=True).start()
    
    bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

    async def telegram_worker():
        while True:
            if msg_queue and monitor.is_connected():
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg_queue[0])
                    msg_queue.pop(0)
                except Exception as e:
                    print(f"Telegram worker error: {e}")
                    await asyncio.sleep(10)
            await asyncio.sleep(5)

    async def reminder_worker():
        while True:
            await asyncio.sleep(settings["repeat_interval"])
            if settings["reminders_on"] and not settings["silent_mode"]:
                bat = psutil.sensors_battery()
                if (bat and not bat.power_plugged) or not monitor.is_connected():
                    msg = "Reminder: System is still in an alert state."
                    asyncio.run_coroutine_threadsafe(voice.speak(msg, monitor.is_connected(), False), loop)

    def handle_alert(alert_type, msg, color):
        logger.log(msg, is_alert=True, max_mb=settings["max_log_mb"])
        if not settings["silent_mode"]:
            overlay = FlashOverlay(color, msg)
            overlay.show()
        
        asyncio.run_coroutine_threadsafe(
            voice.speak(msg, monitor.is_connected(), settings["silent_mode"]), 
            loop
        )
        
        if bot:
            msg_queue.append(f"⚠️ {msg}")

    monitor = MonitorThread(settings)
    monitor.alert_signal.connect(handle_alert)
    monitor.start()

    # STARTUP SCAN LOGIC
    if settings["check_on_start"]:
        def startup_report():
            time.sleep(2)
            is_on = monitor.is_connected()
            bat = psutil.sensors_battery()
            
            p_text = "Power status unknown."
            if bat:
                p_text = "Power is connected." if bat.power_plugged else f"Running on battery at {bat.percent} percent."
            
            ssid = monitor.get_ssid() if is_on else ""
            n_text = f"Network is online on {ssid}." if is_on else "Warning: Network is offline."
            
            full_msg = f"{p_text} {n_text}"
            asyncio.run_coroutine_threadsafe(voice.speak(full_msg, is_on, settings["silent_mode"]), loop)
            logger.log(f"Initial Startup Scan: {full_msg}")
            
        threading.Thread(target=startup_report, daemon=True).start()

    if bot:
        asyncio.run_coroutine_threadsafe(telegram_worker(), loop)
    
    asyncio.run_coroutine_threadsafe(reminder_worker(), loop)

    def update_setting(key, value):
        settings[key] = value
        if key == "volume":
            voice.set_volume(value)
        if key == "startup":
            set_startup(value)
        with open("settings.json", "w") as f:
            json.dump(settings, f)

    icon = Icon("AuraMonitor", Image.new('RGB', (64, 64), (255, 0, 0)))

    def refresh_menu():
        menu_items = [
            MenuItem(f"Aura Monitor v{APP_VERSION}", lambda: None, enabled=False),
            MenuItem(f"Queued Telegrams: {len(msg_queue)}", lambda: None, enabled=False),
            MenuItem("--- Settings ---", lambda: None, enabled=False),
            MenuItem('Silent Mode', lambda i, it: update_setting("silent_mode", not settings["silent_mode"]), checked=lambda it: settings["silent_mode"]),
            MenuItem('Startup Scan', lambda i, it: update_setting("check_on_start", not settings["check_on_start"]), checked=lambda it: settings["check_on_start"]),
            MenuItem('Start with Windows', lambda i, it: update_setting("startup", not settings["startup"]), checked=lambda it: settings["startup"]),
            MenuItem('Volume 100%', lambda: update_setting("volume", 1.0)),
            MenuItem('Volume 50%', lambda: update_setting("volume", 0.5)),
            MenuItem('Mute', lambda: update_setting("volume", 0.0)),
            MenuItem('Open Alert Log', lambda: os.startfile("alert.log")),
            MenuItem('Open Activity Log', lambda: os.startfile("activity.log")),
            MenuItem('Exit', lambda: os._exit(0))
        ]
        icon.menu = Menu(*menu_items)

    # Note: refresh_menu is static in this pystray implementation but we can call it on startup
    refresh_menu()
    threading.Thread(target=icon.run, daemon=False).start()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
    # ---------------------End of code-----------------------------
