import sys, os, asyncio, psutil, socket, datetime, threading, subprocess, pygame, json, winreg, time, requests, webbrowser, logging
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QMessageBox
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from telegram import Bot
import edge_tts
from pystray import Icon, MenuItem, Menu
from PIL import Image
from packaging import version 
import pywifi
from pywifi import const
from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
import keyring
import ctypes
from ctypes import wintypes

# 1. DIRECTORY CONFIG
if getattr(sys, 'frozen', False):
    user_dir = os.path.dirname(sys.executable)
else:
    user_dir = os.path.dirname(os.path.abspath(__file__))

# 2. LOGGING SETUP
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')

console_h = logging.StreamHandler(sys.stdout)
console_h.setFormatter(formatter)
logger.addHandler(console_h)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logging.getLogger('pywifi').setLevel(logging.WARNING)

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

TELEGRAM_TOKEN = keyring.get_password("AuraMonitor", "TELEGRAM_TOKEN")
CHAT_ID = keyring.get_password("AuraMonitor", "CHAT_ID")
APP_VERSION = "1.4.1"
active_overlay = None

def check_for_updates(current_version):
    api_url = "https://api.github.com/repos/aura-1331/monitor-app/releases/latest"
    try:
        response = requests.get(api_url, headers={"User-Agent": "AuraMonitor"}, timeout=5)
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
        try:
            subprocess.run('taskkill /f /im piper.exe', shell=True, capture_output=True)
        except: pass

    async def speak(self, text, is_online, muted):
        if muted: return
        adv_logger.log(f"Speaking: {text}")
        is_critical = ("CRITICAL" in text or "POWER LOST" in text or "not charging" in text.lower())
        if is_critical:
            self.stop_audio()
        if not is_critical:
            while pygame.mixer.get_init() and pygame.mixer.music.get_busy():
                await asyncio.sleep(0.5)
        self.cleanup_temp_files()
        await asyncio.sleep(0.2)
        ts = int(time.time())
        filename = os.path.join(self.base_dir, f"v_{ts}")
        try:
            if is_online:
                adv_logger.log("Requesting Edge-TTS (Ana)...")
                try:
                    comm = edge_tts.Communicate(text, "en-US-AnaNeural")
                    await asyncio.wait_for(comm.save(filename + ".mp3"), timeout=6.0)
                    final_file = filename + ".mp3"
                except Exception as e:
                    adv_logger.log(f"Edge-TTS Error: {e}")
                    is_online = False
            if not is_online:
                adv_logger.log("Using Piper Offline TTS...")
                final_file = filename + ".wav"
                cmd = f'echo {text} | "{self.piper_exe}" --model "{self.model}" --output_file "{final_file}"'
                subprocess.check_call(cmd, shell=True)
            if os.path.exists(final_file):
                await asyncio.sleep(0.1)
                pygame.mixer.music.load(final_file)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
        except Exception as e:
            adv_logger.log(f"Voice Engine Failure: {e}")

class AdvancedLogger:
    def __init__(self, activity_file="activity.log", alert_file="alert.log"):
        self.activity_file = os.path.join(user_dir, activity_file)
        self.alert_file = os.path.join(user_dir, alert_file)

    def log(self, message, is_alert=False, max_mb=5):
        now = datetime.datetime.now()
        timestamp = now.strftime("%d-%m-%Y %H:%M:%S")
        date_header = now.strftime("%d-%m-%Y %A ---->")
        tags = {"POWER LOST": "[⚡-]", "POWER RESTORED": "[⚡+]", "NETWORK LOST": "[🌐-]", "NETWORK RESTORED": "[🌐+]"}
        tag = next((v for k, v in tags.items() if k in message), "[ACTION]")
        full_msg = f"{timestamp} {tag} {message}"
        print(full_msg)
        targets = [self.activity_file]
        if is_alert: targets.append(self.alert_file)
        for f_path in targets:
            try:
                if os.path.exists(f_path) and os.path.getsize(f_path) > (max_mb * 1024 * 1024):
                    with open(f_path, 'w', encoding='utf-8') as f: 
                        f.write(f"--- LOG RESET: {timestamp} (Size Limit) ---\n")
                content = ""
                if os.path.exists(f_path):
                    with open(f_path, 'r', encoding='utf-8') as f: content = f.read()
                if date_header not in content:
                    with open(f_path, 'a', encoding='utf-8') as f:
                        f.write(f"\n\n{'#'*50}\n{date_header}\n{'#'*50}\n")
                with open(f_path, 'a', encoding='utf-8') as f: f.write(f"{full_msg}\n")
            except: pass

    def reset_logs(self):
        for f in [self.activity_file, self.alert_file]:
            if os.path.exists(f):
                with open(f, 'w', encoding='utf-8') as file:
                    file.write("")

    def check_missed_reset(self, bot, chat_id, loop):
        if not os.path.exists(self.activity_file): return
        today_str = datetime.datetime.now().strftime("%d-%m-%Y")
        try:
            with open(self.activity_file, 'r', encoding='utf-8') as f:
                content = f.read(2048) # Read beginning
            if any(char.isdigit() for char in content) and today_str not in content:
                adv_logger.log("SYSTEM: Missed midnight reset detected. Sending stale report...")
                send_health_report(self.activity_file, bot, chat_id, loop, is_auto=True)
                time.sleep(3)
                self.reset_logs()
                self.log("--- LOGS RESET (CATCH-UP) ---")
        except: pass

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
        self.is_critical = "CRITICAL BATTERY" in message or "not charging" in message.lower()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setWindowOpacity(0.0)
        self.setStyleSheet(f"background-color: {color};")
        layout = QVBoxLayout()
        self.label = QLabel(message)
        self.label.setStyleSheet("color: white; font-size: 50pt; font-weight: bold; padding: 40px;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.setLayout(layout)
        if not self.is_critical:
            self.fade_in = QPropertyAnimation(self, b"windowOpacity")
            self.fade_in.setDuration(500)
            self.fade_in.setStartValue(0.0)
            self.fade_in.setEndValue(0.8)
            self.fade_out = QPropertyAnimation(self, b"windowOpacity")
            self.fade_out.setDuration(800)
            self.fade_out.setStartValue(0.8)
            self.fade_out.setEndValue(0.0)
            self.fade_out.finished.connect(self.safe_close)
            self.fade_in.start()
            QTimer.singleShot(4000, self.fade_out.start)
        else:
            self.flash_count = 0
            self.flash_timer = QTimer()
            self.flash_timer.timeout.connect(self.flash_screen)
            self.flash_timer.start(400)
    def flash_screen(self):
        if self.flash_count >= 10:
            self.flash_timer.stop()
            self.safe_close()
            return
        self.setWindowOpacity(1.0 if self.windowOpacity() == 0 else 0.0)
        self.flash_count += 1
    def safe_close(self):
        self.close()
        self.deleteLater()

class MonitorThread(QThread):
    alert_signal = pyqtSignal(str, str, str)
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.last_ssid = ""
        self.last_low_alert_time = 0
        self.alerted_80 = self.alerted_90 = self.alerted_100 = False
        self.last_charge_percent = None
        self.last_charge_check_time = time.time()
        self.stuck_charge_alerted = False
        self.internet_down_alerted = False

    def get_ssid(self):
        try:
            cmd = "powershell -Command \"Get-NetConnectionProfile | Where-Object {$_.InterfaceAlias -match 'Wi-Fi'} | Select-Object -ExpandProperty Name\""
            process = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            ssid = process.stdout.strip()
            return ssid if ssid else "Disconnected"
        except: return "Unknown Network"

    def is_connected(self):
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except: return False

    def run(self):
        battery = psutil.sensors_battery()
        if battery and battery.power_plugged:
            if battery.percent >= 80: self.alerted_80 = True
            if battery.percent >= 90: self.alerted_90 = True
            if battery.percent >= 100: self.alerted_100 = True
        lp = battery.power_plugged if battery else None
        ln = self.is_connected()
        self.last_ssid = self.get_ssid() if ln else "None"
        p_time = n_time = time.time()
        while True:
            battery = psutil.sensors_battery()
            p = battery.power_plugged if battery else None
            percent = battery.percent if battery else 0
            s = self.get_ssid()
            n = self.is_connected()
            current_time = time.time()
            if s not in ["Disconnected", "No Adapter", "Unknown Network"] and not n:
                if not self.internet_down_alerted:
                    self.alert_signal.emit("N", f"Connected to {s}, but internet is unavailable.", "yellow")
                    self.internet_down_alerted = True
            elif n: self.internet_down_alerted = False
            if self.settings["reminders_on"] and not p:
                if percent >= 80: interval = 1800
                elif percent >= 50: interval = 600
                elif percent >= 20: interval = 300
                else: interval = 120
                if current_time - self.last_low_alert_time >= interval:
                    if percent < 20: self.alert_signal.emit("P", f"CRITICAL BATTERY {percent}%! Plug in charger immediately!", "darkred")
                    else: self.alert_signal.emit("P", f"Battery is at {percent}%", "red")
                    self.last_low_alert_time = current_time
            # -----------------------------
            # FIXED CHARGING MILESTONE ALERTS
            # -----------------------------
            if self.settings["reminders_on"] and p:
                # Only alert if we haven't alerted for this specific level yet
                if percent >= 100 and not self.alerted_100:
                    self.alert_signal.emit("P", "Battery reached 100%. Please unplug charger.", "green")
                    self.alerted_100 = True
                    self.alerted_90 = True # Mark lower ones as 'done' so they don't fire
                    self.alerted_80 = True

                elif percent >= 90 and percent < 100 and not self.alerted_90:
                    self.alert_signal.emit("P", "Battery reached 90%. Consider unplugging charger.", "green")
                    self.alerted_90 = True
                    self.alerted_80 = True # Mark 80 as 'done'

                elif percent >= 80 and percent < 90 and not self.alerted_80:
                    self.alert_signal.emit("P", "Battery reached 80%. Charging milestone reached.", "green")
                    self.alerted_80 = True
            if p:
                if self.last_charge_percent is None or percent > self.last_charge_percent:
                    self.last_charge_percent, self.last_charge_check_time, self.stuck_charge_alerted = percent, current_time, False
                elif percent == self.last_charge_percent and percent < 95 and current_time - self.last_charge_check_time >= 300 and not self.stuck_charge_alerted:
                    self.alert_signal.emit("P", f"Charger connected but battery is not charging ({percent}%). Check charger.", "darkred")
                    self.stuck_charge_alerted = True
            if n != ln or s != self.last_ssid:
                dur = str(datetime.timedelta(seconds=int(time.time() - n_time)))
                stat = "RESTORED" if n else "LOST"
                final_ssid = s if s not in ["Disconnected", "Identifying...", "Unknown Network"] else "a stable network"
                msg = f"NETWORK {stat} on {final_ssid} - Downtime: {dur}" if n != ln else f"SWITCHED NETWORK to {final_ssid}"
                self.alert_signal.emit("N", msg, "blue" if n else "yellow")
                ln, self.last_ssid, n_time = n, s, time.time()
            if p is not None and p != lp:
                dur = str(datetime.timedelta(seconds=int(time.time() - p_time)))
                msg = f"POWER RESTORED. Battery is at {percent}%. Downtime: {dur}" if p else f"POWER LOST. Running on battery at {percent}%."
                if not p: self.alerted_80 = self.alerted_90 = self.alerted_100 = False
                self.alert_signal.emit("P", msg, "green" if p else "red")
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
    except: pass

def send_health_report(log_path, bot, chat_id, loop, is_auto=False):
    if not os.path.exists(log_path): return
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
        net_lost, pwr_lost = content.count("[🌐-]"), content.count("[⚡-]")
    title = "🌙 End of Day Health Report:" if is_auto else "📊 Manual Health Report:"
    report = f"{title}\n🔌 Power Outages: {pwr_lost}\n🌐 Network Drops: {net_lost}"
    if bot: asyncio.run_coroutine_threadsafe(bot.send_message(chat_id=chat_id, text=report), loop)

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    global adv_logger
    adv_logger = AdvancedLogger()
    adv_logger.log("--- AURA MONITOR STARTED ---")
    voice, msg_queue = VoiceManager(), []
    settings = {"volume": 1.0, "repeat_interval": 300, "reminders_on": True, "startup": False, "silent_mode": False, "max_log_mb": 5, "check_on_start": True}
    settings_path = os.path.join(user_dir, "settings.json")
    if os.path.exists(settings_path):
        with open(settings_path, "r") as f: settings.update(json.load(f))
    voice.set_volume(settings["volume"])
    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda l: (asyncio.set_event_loop(l), l.run_forever()), args=(loop,), daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
    
    adv_logger.check_missed_reset(bot, CHAT_ID, loop)
    
    new_tag, update_url = check_for_updates(APP_VERSION)
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
            msg.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
            msg.exec()
    bridge = UiBridge()

    def handle_alert(alert_type, msg, color):
        is_critical = color in ["red", "darkred"]
        adv_logger.log(f"EVENT DETECTED: {msg}")
        adv_logger.log(msg, is_alert=is_critical, max_mb=settings["max_log_mb"])
        try:
            bat = psutil.sensors_battery()
            if bat and not bat.power_plugged: ni = Image.open(get_resource_path("app_icon_battery.ico"))
            elif not monitor.is_connected(): ni = Image.open(get_resource_path("app_icon_offline.ico"))
            else: ni = Image.open(get_resource_path("app_icon.ico"))
            icon.icon = ni
        except: pass
        if not settings["silent_mode"]:
            adv_logger.log(f"Flash Overlay Triggered ({color})")
            overlay = FlashOverlay(color, msg)
            overlay.show()
        asyncio.run_coroutine_threadsafe(voice.speak(msg, monitor.is_connected(), settings["silent_mode"]), loop)
        if bot: msg_queue.append(f"⚠️ {msg}")

    monitor = MonitorThread(settings)
    monitor.alert_signal.connect(handle_alert)
    monitor.start()

    def midnight_worker():
        while True:
            now = datetime.datetime.now()
            if now.hour == 23 and now.minute == 59 and now.second == 0:
                adv_logger.log("SYSTEM: Midnight maintenance started. Sending report...")
                send_health_report(adv_logger.activity_file, bot, CHAT_ID, loop, is_auto=True)
                time.sleep(5)
                adv_logger.reset_logs()
                adv_logger.log("--- LOGS RESET FOR NEW DAY ---")
                time.sleep(60)
            time.sleep(1)
    threading.Thread(target=midnight_worker, daemon=True).start()

    def startup_report():
        time.sleep(10)
        bat, is_online = psutil.sensors_battery(), monitor.is_connected()
        if bat and not bat.power_plugged and bat.percent < 20: return
        welcome = "Aura's active. I'm watching your system now."
        adv_logger.log(welcome)
        asyncio.run_coroutine_threadsafe(voice.speak(welcome, is_online, settings["silent_mode"]), loop)
        time.sleep(7)
        if settings["check_on_start"]:
            ssid = monitor.get_ssid()
            p_stat = "AC Connected" if bat.power_plugged else f"Battery ({bat.percent}%)"
            n_stat = f"Online on {ssid}" if is_online else "OFFLINE"
            report_msg = f"System check. The system is {p_stat}. {n_stat}."
            adv_logger.log(report_msg)
            asyncio.run_coroutine_threadsafe(voice.speak(report_msg, is_online, settings["silent_mode"]), loop)

    threading.Thread(target=startup_report, daemon=True).start()
    async def telegram_worker():
        while True:
            if msg_queue and monitor.is_connected():
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg_queue[0])
                    adv_logger.log(f"Telegram Message Sent: {msg_queue[0]}")
                    msg_queue.pop(0)
                except: 
                    adv_logger.log("Telegram Failed to send.")
                    await asyncio.sleep(10)
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
            MenuItem('Open Alert Log', lambda: os.startfile(adv_logger.alert_file) if os.path.exists(adv_logger.alert_file) else None),
            MenuItem('Open Activity Log', lambda: os.startfile(adv_logger.activity_file) if os.path.exists(adv_logger.activity_file) else None),
            MenuItem('Send Health Report', lambda: send_health_report(adv_logger.activity_file, bot, CHAT_ID, loop)),
            MenuItem('Exit', lambda: os._exit(0))
        ]
        icon.menu = Menu(*menu_items)

    refresh_menu()
    threading.Thread(target=icon.run, daemon=True).start()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()