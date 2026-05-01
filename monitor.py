import sys, os, asyncio, psutil, socket, datetime, threading, subprocess, pygame, json, winreg, time, requests, webbrowser, logging
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QMessageBox
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from telegram import Bot
import edge_tts
from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from packaging import version 
import pywifi
from pywifi import const
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
import keyring

# 1. DEFINE USER_DIR
if getattr(sys, 'frozen', False):
    user_dir = os.path.dirname(sys.executable)
else:
    user_dir = os.path.dirname(os.path.abspath(__file__))

# 2. INITIALIZE LOGGING SYSTEM
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

console_h = logging.StreamHandler(sys.stdout)
console_h.setFormatter(formatter)
logger.addHandler(console_h)

file_path = os.path.join(user_dir, "activity.log")
file_h = logging.FileHandler(file_path, encoding='utf-8')
file_h.setFormatter(formatter)
logger.addHandler(file_h)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

# --- SILENCE PYWIFI TECHNICAL SPAM (HUMAN READABLE CONSOLE) ---
logging.getLogger('pywifi').setLevel(logging.WARNING)

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# Fetches directly from the encrypted Windows Credential Manager
TELEGRAM_TOKEN = keyring.get_password("AuraMonitor", "TELEGRAM_TOKEN")
CHAT_ID = keyring.get_password("AuraMonitor", "CHAT_ID")
APP_VERSION = "1.3.0"

active_overlay = None

def check_for_updates(current_version):
    api_url = "https://api.github.com/repos/aura-1331/monitor-app/releases/latest"
    try:
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_tag = data["tag_name"]
            if version.parse(latest_tag.lstrip('v')) > version.parse(current_version.lstrip('v')):
                url = data.get("html_url")
                for asset in data.get("assets", []):
                    if asset["name"].endswith(".exe"):
                        url = asset["browser_download_url"]
                        break
                return latest_tag, url
    except Exception as e: 
        logging.error(f"Update check failed: {e}")
    return None, None

class VoiceManager:
    def __init__(self):
        self.piper_exe = get_resource_path(os.path.join("piper", "piper.exe"))
        self.model = get_resource_path(os.path.join("piper", "en_US-lessac-medium.onnx"))
        self.base_dir = user_dir 
        try: pygame.mixer.init()
        except: pass
        self.volume = 1.0
        self.cleanup_temp_files()

    def set_volume(self, vol):
        self.volume = vol
        if pygame.mixer.get_init(): pygame.mixer.music.set_volume(vol)

    def cleanup_temp_files(self):
        try:
            for f in os.listdir(self.base_dir):
                if f.startswith("v_") and (f.endswith(".mp3") or f.endswith(".wav")):
                    try: os.remove(os.path.join(self.base_dir, f))
                    except: pass
        except: pass

    def stop_audio(self):
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception as e:
                logging.debug(f"Audio cleanup error: {e}")
        
        # SELF-HEALING: Force kill any stuck piper.exe processes
        try:
            subprocess.run('taskkill /f /im piper.exe', shell=True, capture_output=True)
        except:
            pass

    async def speak(self, text, is_online, muted):
        if muted: 
            logging.info(f"Voice Muted: {text}")
            return
        logging.info(f"Speaking: {text}")
        self.stop_audio()
        self.cleanup_temp_files()
        await asyncio.sleep(0.2)
        ts = int(time.time())
        use_online = is_online
        filename = os.path.join(self.base_dir, f"v_{ts}")
        try:
            if use_online:
                logging.info("Requesting Edge-TTS (Ana)...")
                try:
                    comm = edge_tts.Communicate(text, "en-US-AnaNeural")
                    await asyncio.wait_for(comm.save(filename + ".mp3"), timeout=7.0)
                    final_file = filename + ".mp3"
                except Exception as e: 
                    logging.warning(f"Edge-TTS fallback: {e}")
                    use_online = False
            if not use_online:
                logging.info("Using Piper Offline TTS...")
                final_file = filename + ".wav"
                cmd = f'echo {text} | "{self.piper_exe}" --model "{self.model}" --output_file "{final_file}"'
                subprocess.check_call(cmd, shell=True)
            if os.path.exists(final_file):
                pygame.mixer.music.load(final_file)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
        except Exception as e: 
            logging.error(f"Voice Error: {e}")

class AdvancedLogger:
    def __init__(self, activity_file="activity.log", alert_file="alert.log"):
        # Setup absolute paths to ensure logs are saved in the app folder
        self.activity_file = os.path.join(user_dir, activity_file)
        self.alert_file = os.path.join(user_dir, alert_file)

    def log(self, message, is_alert=False, max_mb=5):
        now = datetime.datetime.now()
        date_header = now.strftime("%d-%m-%Y %A ---->")
        
        # Visual tags for better scannability in the text file
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
            try:
                # 1. AUTOMATIC SIZE RECOVERY
                # Prevents the log from eating up disk space
                if os.path.exists(f_path) and os.path.getsize(f_path) > (max_mb * 1024 * 1024):
                    with open(f_path, 'w', encoding='utf-8') as f: 
                        f.write(f"--- LOG RESET: {now} (Size Limit Exceeded) ---\n")
                
                # 2. DATE SEPARATOR LOGIC
                # Only adds the big '#' header if the date has changed or file is new
                content = ""
                if os.path.exists(f_path):
                    with open(f_path, 'r', encoding='utf-8') as f: 
                        content = f.read()
                
                if date_header not in content:
                    with open(f_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n\n{'#'*50}\n{date_header}\n{'#'*50}\n")
                
                # 3. SILENT LOGGING
                # Final write of the actual event
                with open(f_path, 'a', encoding='utf-8') as f: 
                    f.write(f"{full_msg}\n")
            
            except Exception as e:
                # SILENT ERROR REPORTING
                # If the disk is full or file is locked, we don't crash the app.
                # We simply print to terminal so the monitor keeps running.
                print(f"Logging Failure: {e}")



class FlashOverlay(QWidget):
    def __init__(self, color, message):
        global active_overlay
        if active_overlay:
            try:
                active_overlay.hide()
                active_overlay.destroy()
            except: pass
        super().__init__()
        active_overlay = self
        
        # Window Setup
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setWindowOpacity(0.0)  # Start fully transparent
        self.setStyleSheet(f"background-color: {color};")
        
        layout = QVBoxLayout()
        self.label = QLabel(message)
        self.label.setStyleSheet("color: white; font-size: 45pt; font-weight: bold; padding: 40px;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.setLayout(layout)

        # Fade In Animation
        self.fade_in = QPropertyAnimation(self, b"windowOpacity")
        self.fade_in.setDuration(500) # 0.5 seconds
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(0.8)
        self.fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Fade Out Animation
        self.fade_out = QPropertyAnimation(self, b"windowOpacity")
        self.fade_out.setDuration(800) # 0.8 seconds
        self.fade_out.setStartValue(0.8)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self.fade_out.finished.connect(self.safe_close)

        # Sequence Control
        self.fade_in.start()
        # Wait 4 seconds then trigger fade out
        QTimer.singleShot(4000, self.fade_out.start)

    def safe_close(self):
        self.close()
        self.deleteLater()

class MonitorThread(QThread):
    alert_signal = pyqtSignal(str, str, str)
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.last_ssid = ""
        try:
            self.wifi = pywifi.PyWiFi()
            self.iface = self.wifi.interfaces()[0]
        except:
            self.iface = None

    def get_ssid(self):
        if not self.iface: return "No Adapter"
        try:
            if self.iface.status() == const.IFACE_CONNECTED:
                profiles = self.iface.network_profiles()
                if profiles: return profiles[0].ssid
            return "Disconnected"
        except: return "Unknown Network"

    def is_connected(self):
        try:
            import urllib.request
            urllib.request.urlopen('http://www.google.com', timeout=3)
            return True
        except: pass
        try:
            socket.gethostbyname("google.com")
            return True
        except: pass
        for target in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
            try:
                socket.create_connection(target, timeout=3)
                return True
            except: continue
        try:
            res = subprocess.run("ping -n 1 -w 1000 8.8.8.8", shell=True, capture_output=True)
            if res.returncode == 0: return True
        except: pass
        return False

    def run(self):
        logging.info("Monitor Thread Active.")
        battery = psutil.sensors_battery()
        lp = battery.power_plugged if battery else None
        ln = self.is_connected()
        self.last_ssid = self.get_ssid() if ln else "None"
        p_time = n_time = time.time()
        while True:
            battery = psutil.sensors_battery()
            p, percent = (battery.power_plugged, battery.percent) if battery else (None, 0)
            s = self.get_ssid()
            n = self.is_connected()
            if n != ln:
                if not n: time.sleep(4); n = self.is_connected()
                if n != ln:
                    dur = str(datetime.timedelta(seconds=int(time.time() - n_time)))
                    stat = "RESTORED" if n else "LOST"
                    logging.info(f"NETWORK {stat} (SSID: {s})")
                    msg = f"NETWORK {stat}" + (f" on {s} - Downtime: {dur}" if n else "")
                    self.alert_signal.emit("N", msg, "blue" if n else "yellow")
                    ln, n_time = n, time.time()
                    self.last_ssid = s
            elif n and s != self.last_ssid and s != "Unknown Network":
                logging.info(f"NETWORK SWITCHED: {s}")
                self.alert_signal.emit("N", f"NETWORK SWITCHED to {s}", "blue")
                self.last_ssid = s
            if p is not None and p != lp:
                dur = str(datetime.timedelta(seconds=int(time.time() - p_time)))
                logging.info(f"POWER EVENT: {'Connected' if p else 'Battery'}")
                if p:
                    msg = f"POWER RESTORED. Battery is at {percent}%." + f" Downtime: {dur}"
                    color = "green"
                else:
                    msg = f"POWER LOST. Running on battery at {percent}%."
                    color = "red"
                self.alert_signal.emit("P", msg, color)
                lp, p_time = p, time.time()
            self.msleep(2000)

def set_startup(enabled):
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    exe_path = f'"{os.path.join(user_dir, "monitor.exe")}"'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        if enabled: winreg.SetValueEx(key, "AuraMonitor", 0, winreg.REG_SZ, exe_path)
        else: winreg.DeleteValue(key, "AuraMonitor")
        winreg.CloseKey(key)
    except Exception as e: logging.error(f"Startup error: {e}")

def send_health_report(log_path, bot, chat_id, loop):
    if not os.path.exists(log_path): return
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
        net_lost = content.count("[🌐-]")
        pwr_lost = content.count("[⚡-]")
    report = f"📊 Health Report:\n🔌 Power Outages: {pwr_lost}\n🌐 Network Drops: {net_lost}"
    asyncio.run_coroutine_threadsafe(bot.send_message(chat_id=chat_id, text=report), loop)

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # --- RENAME ADV_LOGGER TO AVOID CONFLICT WITH GLOBAL LOGGER ---
    adv_logger = AdvancedLogger() 
    voice = VoiceManager()
    msg_queue = []
    settings = {"volume": 1.0, "repeat_interval": 300, "reminders_on": True, "startup": False, "silent_mode": False, "max_log_mb": 5, "check_on_start": True}
    
    settings_path = os.path.join(user_dir, "settings.json")
    if os.path.exists(settings_path):
        with open(settings_path, "r") as f: settings.update(json.load(f))
    voice.set_volume(settings["volume"])
    
    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda l: (asyncio.set_event_loop(l), l.run_forever()), args=(loop,), daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
    new_tag, update_url = check_for_updates(APP_VERSION)

    # Define Icon BEFORE monitor/alert logic
    icon_image = Image.open(get_resource_path("app_icon.ico"))
    icon = Icon("AuraMonitor", icon_image, f"Aura Monitor v{APP_VERSION}")

    class UiBridge(QWidget):
        show_about_signal = pyqtSignal()
        def __init__(self):
            super().__init__()
            self.show_about_signal.connect(self.show_about_on_main)
        def show_about_on_main(self):
            msg = QMessageBox()
            msg.setWindowTitle("About Aura Monitor")
            msg.setText(f"Aura Monitor v{APP_VERSION}\n\nProfessional System Utility.\nDeveloped by Aura.")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
            msg.exec()

    bridge = UiBridge()

    def handle_alert(alert_type, msg, color):
        adv_logger.log(msg, is_alert=True, max_mb=settings["max_log_mb"])
        
        try:
            # Determine the correct image based on priority
            if not psutil.sensors_battery().power_plugged:
                new_icon_img = Image.open(get_resource_path("app_icon_battery.ico"))
            elif not monitor.is_connected():
                new_icon_img = Image.open(get_resource_path("app_icon_offline.ico"))
            else:
                new_icon_img = Image.open(get_resource_path("app_icon.ico"))
            
            # Force the update and tell Windows to redraw
            icon.icon = new_icon_img
            
        except Exception as e:
            logging.error(f"Icon swap failed: {e}")

        if not settings["silent_mode"]:
            overlay = FlashOverlay(color, msg)
            overlay.show()
        
        asyncio.run_coroutine_threadsafe(voice.speak(msg, monitor.is_connected(), settings["silent_mode"]), loop)
        if bot: msg_queue.append(f"⚠️ {msg}")

    monitor = MonitorThread(settings)
    monitor.alert_signal.connect(handle_alert)
    monitor.start()

    def startup_report():
        logging.info("--- AURA MONITOR INITIALIZING ---")
        time.sleep(5) 
        welcome = "Aura's active. I'm watching your system now."
        is_online = monitor.is_connected()
        asyncio.run_coroutine_threadsafe(voice.speak(welcome, is_online, settings["silent_mode"]), loop)
        time.sleep(7) 
        if settings["check_on_start"]:
            bat = psutil.sensors_battery()
            ssid = monitor.get_ssid()
            p_stat = 'AC Connected' if bat.power_plugged else f'Battery ({bat.percent}%)'
            n_stat = f'Online ({ssid})' if is_online else 'OFFLINE'
            report_msg = f"System check. The system is {p_stat}. {n_stat}."
            asyncio.run_coroutine_threadsafe(voice.speak(report_msg, is_online, settings["silent_mode"]), loop)

    threading.Thread(target=startup_report, daemon=True).start()
    
    async def telegram_worker():
        while True:
            if msg_queue and monitor.is_connected():
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg_queue[0])
                    msg_queue.pop(0)
                except: await asyncio.sleep(10)
            await asyncio.sleep(5)
    if bot: asyncio.run_coroutine_threadsafe(telegram_worker(), loop)

    def refresh_menu():
        menu_items = [
            MenuItem(f"Aura Monitor v{APP_VERSION}", lambda: None, enabled=False),
            MenuItem(f"Queued Telegrams: {len(msg_queue)}", lambda: None, enabled=False),
            MenuItem(f"✨ Update Available", lambda: webbrowser.open(update_url)) if new_tag else MenuItem("Up to date", lambda: None, enabled=False),
            MenuItem("--- Settings ---", lambda: None, enabled=False),
            MenuItem('Silent Mode', lambda i, it: (settings.update({"silent_mode": not settings["silent_mode"]}), refresh_menu()), checked=lambda it: settings["silent_mode"]),
            MenuItem('Startup Scan', lambda i, it: (settings.update({"check_on_start": not settings["check_on_start"]}), refresh_menu()), checked=lambda it: settings["check_on_start"]),
            MenuItem('Start with Windows', lambda i, it: (set_startup(not settings["startup"]), settings.update({"startup": not settings["startup"]}), refresh_menu()), checked=lambda it: settings["startup"]),
            MenuItem('--- Info ---', lambda: None, enabled=False),
            MenuItem('About', lambda: bridge.show_about_signal.emit()),
            # USE ADV_LOGGER TO ACCESS LOG FILE PATHS
            MenuItem('Open Alert Log', lambda: os.startfile(adv_logger.alert_file)),
            MenuItem('Open Activity Log', lambda: os.startfile(adv_logger.activity_file)),
            MenuItem('Send Health Report', lambda: send_health_report(adv_logger.activity_file, bot, CHAT_ID, loop)),
            MenuItem('Exit', lambda: os._exit(0))
        ]
        icon.menu = Menu(*menu_items)

    refresh_menu()
    threading.Thread(target=icon.run, daemon=True).start()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()