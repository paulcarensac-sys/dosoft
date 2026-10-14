import os
import sys
import logging
import ctypes
import threading
import keyboard
import time
import win32api
import win32con
import win32gui
import win32process
from PIL import Image
import tkinter as tk
import customtkinter as ctk
import pystray
from pystray import MenuItem as item
import asyncio
import requests
from tkinter import messagebox 
from app_version import CURRENT_VERSION, APP_TITLE
from app_logging import logger, setup_logging, log_throttled, log_exception_throttled

# --- FORCAGE DU DPI AWARENESS (Règle les soucis 4K / Zoom Windows) ---
try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except: pass

try:
    from winrt.windows.ui.notifications.management import UserNotificationListener
    from winrt.windows.ui.notifications import NotificationKinds
    WINSDK_AVAILABLE = True
except ImportError:
    try:
        from winsdk.windows.ui.notifications.management import UserNotificationListener
        from winsdk.windows.ui.notifications import NotificationKinds
        WINSDK_AVAILABLE = True
    except ImportError:
        WINSDK_AVAILABLE = False

from config_manager import Config
from logic import DofusLogic
from gui import OrganizerGUI
from radial_menu import RadialMenu
from i18n_manager import I18nManager
from keyboard_layout_manager import KeyboardLayoutManager


def try_load_windows_layout(layout_name):
    layout_map = {
        "azerty_fr": "0000040C",
        "qwerty_us": "00000409",
    }
    layout_code = layout_map.get(layout_name)
    if not layout_code:
        return
    try:
        ctypes.windll.user32.LoadKeyboardLayoutW(layout_code, 1 | 0x00000100)
    except Exception:
        pass


class OrganizerApp:
    def __init__(self):
        self.config = Config()
        self.i18n = I18nManager(self.config.data.get("language", "fr"))
        self.keymaps = KeyboardLayoutManager(self.config.data.get("keyboard_layout", "azerty_fr"))
        try_load_windows_layout(self.config.data.get("keyboard_layout", "azerty_fr"))
        self.logic = DofusLogic(self.config)
        self.gui = OrganizerGUI(self)
        default_report = self.gui.root.report_callback_exception
        def report_tk_exception(exc_type, exc_value, exc_tb):
            logger.error("Exception dans un callback Tkinter", exc_info=(exc_type, exc_value, exc_tb))
            default_report(exc_type, exc_value, exc_tb)
        self.gui.root.report_callback_exception = report_tk_exception
        self.current_idx = 0
        self.hotkey_actions = {}
        self.mouse_hotkeys = {}
        self.mouse_states = {}
        self.hotkey_labels = {}
        
        self.radial_focus = RadialMenu(self.gui.root, self.on_radial_focus_select, accent_color="#2ecc71", hover_color="#27ae60", center_icon_path="skin/character.png")
        
        saved_vol = self.config.data.get("volume_level", 50) / 100.0
        self.radial_focus.set_base_volume(saved_vol)
        
        threading.Thread(target=self.background_listener, daemon=True).start()
        
        self.setup_hotkeys()
        self.refresh()
        self.start_notification_listener()
        self.setup_system_tray()
        
        self.gui.root.after(1000, self.check_conflicting_software)

        if not self.config.data.get("tutorial_done", False):
            self.gui.root.after(800, self.gui.launch_tutorial)
    
    def setup_system_tray(self):
        icon_path = "logo.ico" 
        try: image = Image.open(icon_path)
        except: image = Image.new('RGB', (64, 64), color=(44, 62, 80))

        menu = pystray.Menu(
            item(self.i18n.t("tray_toggle", "Afficher/Cacher"), self.toggle_from_tray, default=True),
            item(self.i18n.t("tray_sort", "Trier Barre Windows"), self.sort_taskbar_from_tray),
            item(self.i18n.t("tray_refresh", "Rafraîchir"), self.refresh_from_tray),
            item(self.i18n.t("tray_quit", "Quitter"), self.quit_from_tray)
        )
        self.tray_icon = pystray.Icon("dosoft_tray", image, "DOSOFT", menu)
        self.tray_icon.run_detached()

    def toggle_from_tray(self, icon, item):
        def safe_toggle():
            if self.gui.root.state() == 'withdrawn':
                self.gui.root.deiconify()
                self.gui.root.lift()
                self.gui.root.focus_force()
            else:
                self.gui.root.withdraw()
        self.gui.root.after(0, safe_toggle)

    def refresh_from_tray(self, icon, item):
        self.gui.root.after(0, self.refresh)

    def sort_taskbar_from_tray(self, icon, item):
        self.gui.root.after(0, self.gui.trigger_sort_taskbar)

    def quit_from_tray(self, icon, item):
        self.tray_icon.stop() 
        self.gui.root.after(0, self.gui.root.destroy) 

    def update_volume(self, volume_val):
        self.config.data["volume_level"] = volume_val
        self.config.save()
        vol_float = volume_val / 100.0
        self.radial_focus.set_base_volume(vol_float)

    def check_conflicting_software(self):
        if self.config.data.get("ignore_organizer_warning", False):
            return
        try:
            output = os.popen('tasklist /FI "IMAGENAME eq organizer.exe" /NH').read().lower()
            if "organizer.exe" in output:
                self.show_conflict_popup()
        except Exception: pass

    def show_conflict_popup(self):
        popup = ctk.CTkToplevel(self.gui.root)
        popup.title(self.i18n.t("popup_conflict_title", "⚠️ Conflit de logiciels détecté"))
        popup.geometry("480x250")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.transient(self.gui.root)
        
        popup.update_idletasks()
        x = self.gui.root.winfo_x() + (self.gui.root.winfo_width() // 2) - (480 // 2)
        y = self.gui.root.winfo_y() + (self.gui.root.winfo_height() // 2) - (250 // 2)
        popup.geometry(f"+{x}+{y}")

        msg = self.i18n.t(
            "popup_conflict_text",
            "Le logiciel 'Organizer' est actuellement ouvert.\nL'utilisation de deux gestionnaires de pages simultanément\nva créer des bugs et des conflits de focus sur DOSOFT.\n\nNous vous recommandons fortement de le fermer."
        )
        
        lbl = ctk.CTkLabel(popup, text=msg, justify="center", font=ctk.CTkFont(size=13))
        lbl.pack(pady=(20, 15))

        var_ignore = ctk.BooleanVar(value=False)
        chk = ctk.CTkCheckBox(popup, text=self.i18n.t("popup_conflict_ignore", "Ne plus m'afficher cet avertissement"), variable=var_ignore)
        chk.pack(pady=(0, 20))

        frame_btn = ctk.CTkFrame(popup, fg_color="transparent")
        frame_btn.pack(fill="x", padx=20)

        def on_close_organizer():
            if var_ignore.get():
                self.config.data["ignore_organizer_warning"] = True
                self.config.save()
            os.system("taskkill /F /IM organizer.exe /T")
            popup.destroy()
            self.gui.show_temporary_message(self.i18n.t("msg_organizer_closed", "✅ Organizer fermé avec succès !"), "#2ecc71")

        def on_keep_organizer():
            if var_ignore.get():
                self.config.data["ignore_organizer_warning"] = True
                self.config.save()
            popup.destroy()

        btn_close = ctk.CTkButton(frame_btn, text=self.i18n.t("btn_close_organizer", "Fermer Organizer"), fg_color="#27ae60", hover_color="#2ecc71", command=on_close_organizer)
        btn_close.pack(side="left", expand=True, padx=10)

        btn_keep = ctk.CTkButton(frame_btn, text=self.i18n.t("btn_keep_running", "Conserver"), fg_color="#7f8c8d", hover_color="#95a5a6", command=on_keep_organizer)
        btn_keep.pack(side="right", expand=True, padx=10)
        
        popup.grab_set()

    def get_vk(self, key_str):
        key_str = key_str.lower().strip()
        mapping = {
            "alt": win32con.VK_MENU, "ctrl": win32con.VK_CONTROL, "shift": win32con.VK_SHIFT,
            "left_click": 0x01, "right_click": 0x02, "middle_click": 0x04,
            "mouse4": 0x05, "mouse5": 0x06
        }
        if key_str in mapping: return mapping[key_str]
        
        scan_code = self.keymaps.key_to_scan(key_str)
        if scan_code is not None:
            vk = ctypes.windll.user32.MapVirtualKeyW(scan_code, 1) 
            if vk: return vk
            
        if len(key_str) == 1:
            vk_scan = ctypes.windll.user32.VkKeyScanW(ord(key_str))
            vk = vk_scan & 0xFF
            if vk != 0xFF:
                return vk
            return ord(key_str.upper())
        if key_str.startswith('f') and key_str[1:].isdigit():
            return 0x6F + int(key_str[1:])
        return None

    def is_hotkey_pressed(self, hk_str):
        if not hk_str: return False
        parts = hk_str.split('+')
        for p in parts:
            token = p.lower().strip()
            if token in {"ctrl", "alt", "shift"}:
                vk = self.get_vk(token)
                if vk is None or win32api.GetAsyncKeyState(vk) >= 0:
                    return False
                continue

            mouse_vk = self.get_vk(token) if "click" in token or "mouse" in token else None
            if mouse_vk is not None:
                if win32api.GetAsyncKeyState(mouse_vk) >= 0:
                    return False
                continue

            scan_code = self.keymaps.resolve_scan_code(token)
            if scan_code is not None:
                if not keyboard.is_pressed(scan_code):
                    return False
            elif not keyboard.is_pressed(token):
                return False
        return True

    def background_listener(self):
        radial_was_open = False
        
        while True:
            # Gestion des macros de souris (MB4, MB5, Clics)
            if hasattr(self, 'mouse_hotkeys'):
                for hk_str, func in self.mouse_hotkeys.items():
                    is_pressed = self.is_hotkey_pressed(hk_str)
                    was_pressed = self.mouse_states.get(hk_str, False)
                    
                    if is_pressed and not was_pressed:
                        self.mouse_states[hk_str] = True
                        logger.info("Raccourci souris déclenché : %s -> %s", hk_str, self.hotkey_labels.get(hk_str, "?"))
                        def safe_mouse_execute(f=func):
                            self.release_modifiers()
                            f()
                        threading.Thread(target=safe_mouse_execute, daemon=True).start()
                    elif not is_pressed and was_pressed:
                        self.mouse_states[hk_str] = False

            # Gestion de la roue radiale
            radial_hk = self.config.data.get("radial_menu_hotkey", "")
            radial_active = self.config.data.get("radial_menu_active", True)
            
            if radial_active and radial_hk:
                is_pressed = self.is_hotkey_pressed(radial_hk)
                
                if is_pressed and not radial_was_open:
                    radial_was_open = True
                    is_retro = self.config.data.get("game_version", "Unity") == "Rétro"
                    
                    # Préparation des têtes Retro ou Unity
                    active_accs = []
                    for acc in self.logic.get_cycle_list():
                        cls = acc.get('classe', 'Inconnu')
                        if is_retro and cls != "Inconnu": cls = f"{cls}_retro"
                        active_accs.append({'name': acc['name'], 'classe': cls, 'hwnd': acc['hwnd']})
                    
                    fg_hwnd = win32gui.GetForegroundWindow()
                    current_name = None
                    for acc in active_accs:
                        if acc['hwnd'] == fg_hwnd:
                            current_name = acc['name']
                            break
                            
                    x, y = win32api.GetCursorPos()
                    logger.info("Menu radial ouvert (%d perso(s), actif : %s)", len(active_accs), current_name)
                    self.gui.root.after(0, self.radial_focus.show, x, y, active_accs, current_name)
                    
                elif radial_was_open and not is_pressed:
                    radial_was_open = False
                    self.gui.root.after(0, self.radial_focus.hide)

            # Synchronisation de l'index au clic manuel
            try:
                fg_hwnd = win32gui.GetForegroundWindow()
                cycle_list = self.logic.get_cycle_list()
                if cycle_list:
                    for index, acc in enumerate(cycle_list):
                        if acc['hwnd'] == fg_hwnd:
                            if self.current_idx != index:
                                logger.info("Focus manuel détecté : %s (position %d)", acc['name'], index + 1)
                                self.current_idx = index
                            break
            except Exception:
                log_exception_throttled("sync_focus", "Erreur pendant la synchronisation du focus")
            time.sleep(0.01)

    def on_radial_focus_select(self, target_name):
        logger.info("Menu radial : sélection de %s", target_name)
        for acc in self.logic.all_accounts:
            if acc['name'] == target_name:
                self.logic.focus_window(acc['hwnd'])
                break
        
        cycle_list = self.logic.get_cycle_list()
        for index, acc in enumerate(cycle_list):
            if acc['name'] == target_name:
                self.current_idx = index
                break

    def register_action(self, hk_str, func, label=None):
        if not hk_str: return
        hk_str = hk_str.lower().strip()
        label = label or getattr(func, "__name__", "?")

        if 'click' in hk_str or 'mouse' in hk_str:
            self.mouse_hotkeys[hk_str] = func
            self.hotkey_labels[hk_str] = label
            logger.info("Raccourci souris enregistré : %s -> %s", hk_str, label)
            return
            
        parts = hk_str.split('+')
        mods = set()
        main_scan = None
        for p in parts:
            if p in ['ctrl', 'alt', 'shift']:
                mods.add(p)
                continue
            main_scan = self.keymaps.resolve_scan_code(p)
        if main_scan is not None:
            self.hotkey_actions[(frozenset(mods), main_scan)] = func
            self.hotkey_labels[(frozenset(mods), main_scan)] = f"{hk_str} -> {label}"
            logger.info("Raccourci clavier enregistré : %s -> %s (scan code %s)", hk_str, label, main_scan)
        else:
            logger.warning("Raccourci non reconnu, ignoré : %s -> %s", hk_str, label)

    def _execute_advanced_and_update(self, mode, identifier):
        new_idx = self.logic.execute_advanced_bind(mode, identifier)
        if new_idx != -1: 
            self.current_idx = new_idx

    def release_modifiers(self):
        """Force Windows à relâcher les touches Ctrl, Alt et Shift après la saisie."""
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        except: pass

    def restore_modifiers(self, mods):
        try:
            if 'alt' in mods: win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            if 'ctrl' in mods: win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            if 'shift' in mods: win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        except: pass

    def global_hook_listener(self, event):
        try:
            self._handle_hook_event(event)
        except Exception:
            # La lib keyboard intercepte déjà l'exception (traceback sur stderr) : on la journalise avant.
            logger.exception("Erreur dans le hook clavier")
            raise

    def _handle_hook_event(self, event):
        if event.event_type != keyboard.KEY_DOWN: return
        
        current_mods = set()
        if win32api.GetAsyncKeyState(win32con.VK_CONTROL) < 0: current_mods.add('ctrl')
        if win32api.GetAsyncKeyState(win32con.VK_MENU) < 0: current_mods.add('alt')
        if win32api.GetAsyncKeyState(win32con.VK_SHIFT) < 0: current_mods.add('shift')
        
        key = (frozenset(current_mods), event.scan_code)
        if key in self.hotkey_actions:
            # Seuls les raccourcis reconnus sont journalisés, jamais les autres touches.
            logger.info("Raccourci déclenché : %s", self.hotkey_labels.get(key, "?"))
            def safe_execute(mods=current_mods):
                self.release_modifiers()
                self.hotkey_actions[key]()  # une exception ici est journalisée par threading.excepthook
                time.sleep(0.05)
                self.restore_modifiers(mods)
            threading.Thread(target=safe_execute, daemon=True).start()

    def setup_hotkeys(self):
        keyboard.unhook_all()
        self.hotkey_actions = {} 
        self.mouse_hotkeys = {}
        self.mouse_states = {}
        self.hotkey_labels = {}

        cfg = self.config.data

        # Binds avancés dynamiques
        mode = cfg.get("advanced_bind_mode", "cycle")
        logger.info("Configuration des raccourcis (mode avancé : %s, disposition : %s)", mode, cfg.get("keyboard_layout", "azerty_fr"))
        if mode == "cycle":
            row_binds = cfg.get("cycle_row_binds", [])
            for index, bind_str in enumerate(row_binds):
                if bind_str:
                    self.register_action(bind_str, lambda idx=index: self._execute_advanced_and_update("cycle", idx), f"position {index + 1}")
        elif mode == "bind":
            char_binds = cfg.get("persistent_character_binds", {})
            for pseudo, bind_str in char_binds.items():
                if bind_str:
                    self.register_action(bind_str, lambda ps=pseudo: self._execute_advanced_and_update("bind", ps), f"perso {pseudo}")

        try:
            if cfg.get("prev_key"): self.register_action(cfg["prev_key"], self.prev_char)
            if cfg.get("next_key"): self.register_action(cfg["next_key"], self.next_char)
            if cfg.get("leader_key"): self.register_action(cfg["leader_key"], self.focus_leader)
            if cfg.get("toggle_app_key"): self.register_action(cfg["toggle_app_key"], lambda: self.gui.root.after(0, self.gui.toggle_visibility), "toggle_app")
            if cfg.get('refresh_key'): self.register_action(cfg['refresh_key'], self.refresh)
            if cfg.get('quit_key'):    self.register_action(cfg['quit_key'],   self.quit_app)
            keyboard.hook(self.global_hook_listener)
        except Exception:
            logger.exception("Erreur pendant l'enregistrement des raccourcis")
        logger.info(
            "Menu radial : %s (raccourci : %s)",
            "actif" if cfg.get("radial_menu_active", True) else "inactif", cfg.get("radial_menu_hotkey", "")
        )

    def refresh(self): 
        slots = self.logic.scan_slots()
        self.gui.root.after(0, self.gui.refresh_list, slots)
    
    def focus_leader(self):
        if self.logic.leader_hwnd: 
            self.logic.focus_window(self.logic.leader_hwnd)
            cycle_list = self.logic.get_cycle_list()
            leader_name = self.config.data.get("leader_name", "")
            for index, acc in enumerate(cycle_list):
                if acc['name'] == leader_name:
                    self.current_idx = index
                    break

    def next_char(self):
        cycle_list = self.logic.get_cycle_list()
        if not cycle_list: return
        self.current_idx = (self.current_idx + 1) % len(cycle_list)
        self.logic.focus_window(cycle_list[self.current_idx]['hwnd'])

    def prev_char(self):
        cycle_list = self.logic.get_cycle_list()
        if not cycle_list: return
        self.current_idx = (self.current_idx - 1) % len(cycle_list)
        self.logic.focus_window(cycle_list[self.current_idx]['hwnd'])

    def quit_app(self):
        my_pid = os.getpid()
        os.system(f"taskkill /F /PID {my_pid} /T")

    def start_notification_listener(self):
        if not WINSDK_AVAILABLE:
            logger.info("Moteur de notifications indisponible (ni winrt ni winsdk)")
            return

        def run_async_loop():
            try:
                import pythoncom
                pythoncom.CoInitializeEx(0, pythoncom.COINIT_MULTITHREADED)
            except Exception:
                logger.warning("CoInitializeEx a échoué", exc_info=True)

            mode = self.config.data.get("notif_api_mode", "v2")
            if mode == "v1":
                engine = self.poll_notifications
            elif mode == "v3":
                engine = self._poll_notifications_v3
            else:
                engine = self._poll_notifications_v2
            logger.info("Moteur de notifications démarré (%s)", mode)

            while True:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(engine())
                except Exception:
                    log_exception_throttled("notif_loop", "Le moteur de notifications s'est arrêté, relance dans 3 s")
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
                time.sleep(3)

        threading.Thread(target=run_async_loop, daemon=True).start()

    async def poll_notifications(self):
        while True:
            try:
                listener = UserNotificationListener.current
                access = await listener.request_access_async()
                if access != 1:
                    log_throttled("notif_access", logging.WARNING, "Accès aux notifications Windows refusé (statut %s)", access, interval=300)
                    await asyncio.sleep(5)
                    continue

                seen_ids = set()
                first_pass = True

                while True:
                    try:
                        is_retro = self.config.data.get("game_version", "Unity") == "Rétro"
                        is_autofocus_on = self.config.data.get("auto_focus_retro", False)
                        notifs = await listener.get_notifications_async(NotificationKinds.TOAST)
                        current_ids = set()

                        for n in notifs:
                            current_ids.add(n.id)
                            if n.id not in seen_ids:
                                seen_ids.add(n.id)
                                if not first_pass and is_retro and is_autofocus_on:
                                    try:
                                        binding = n.notification.visual.bindings[0]
                                        texts = [t.text for t in binding.get_text_elements()]
                                        for ligne in texts:
                                            if " - Dofus Retro" in ligne:
                                                pseudo = ligne.split(" - ")[0].strip()
                                                cycle_list = self.logic.get_cycle_list()
                                                for index, acc in enumerate(cycle_list):
                                                    if acc['name'] == pseudo:
                                                        logger.info("Notification Rétro : focus sur %s", pseudo)
                                                        self.gui.root.after(0, self.logic.focus_window, acc['hwnd'])
                                                        self.current_idx = index
                                                        break
                                                break
                                    except Exception:
                                        log_exception_throttled("notif_parse", "Notification illisible")

                        seen_ids.intersection_update(current_ids)
                        first_pass = False
                    except Exception:
                        log_exception_throttled("notif_poll", "Erreur pendant la lecture des notifications")
                    await asyncio.sleep(0.5)
            except Exception:
                log_exception_throttled("notif_listener", "Erreur du moteur de notifications")
            await asyncio.sleep(3)

    # ==========================================
    # MOTEUR V2 : AVEC NETTOYAGE NOTIFICATIONS
    # ==========================================
    async def _poll_notifications_v2(self):
        while True:
            try:
                listener = UserNotificationListener.current
                access = await listener.request_access_async()
                if access != 1:
                    log_throttled("notif_access", logging.WARNING, "Accès aux notifications Windows refusé (statut %s)", access, interval=300)
                    await asyncio.sleep(5)
                    continue

                seen_ids = set()
                first_pass = True

                async def remove_notif_delayed(notif_id):
                    await asyncio.sleep(1.0)
                    try:
                        listener.remove_notification(notif_id)
                    except Exception:
                        log_exception_throttled("notif_remove", "Impossible de retirer la notification")

                while True:
                    try:
                        is_retro = self.config.data.get("game_version", "Unity") == "Rétro"
                        is_autofocus_on = self.config.data.get("auto_focus_retro", False)
                        notifs = await listener.get_notifications_async(NotificationKinds.TOAST)
                        current_ids = set()

                        for n in notifs:
                            current_ids.add(n.id)
                            if n.id not in seen_ids:
                                seen_ids.add(n.id)
                                if not first_pass and is_retro and is_autofocus_on:
                                    try:
                                        binding = n.notification.visual.bindings[0]
                                        texts = [t.text for t in binding.get_text_elements()]
                                        is_dofus_notif = False
                                        for ligne in texts:
                                            if " - Dofus Retro" in ligne:
                                                is_dofus_notif = True
                                                pseudo = ligne.split(" - ")[0].strip()
                                                cycle_list = self.logic.get_cycle_list()
                                                for index, acc in enumerate(cycle_list):
                                                    if acc['name'] == pseudo:
                                                        logger.info("Notification Rétro : focus sur %s", pseudo)
                                                        self.gui.root.after(0, self.logic.focus_window, acc['hwnd'])
                                                        self.current_idx = index
                                                        break
                                                break
                                        if is_dofus_notif:
                                            asyncio.create_task(remove_notif_delayed(n.id))
                                    except Exception:
                                        log_exception_throttled("notif_parse", "Notification illisible")

                        seen_ids.intersection_update(current_ids)
                        first_pass = False
                    except Exception:
                        log_exception_throttled("notif_poll", "Erreur pendant la lecture des notifications")
                    await asyncio.sleep(0.5)
            except Exception:
                log_exception_throttled("notif_listener", "Erreur du moteur de notifications")
            await asyncio.sleep(3)

    # ==========================================
    # MOTEUR V3 : POLLING RAPIDE SANS NETTOYAGE
    # ==========================================
    async def _poll_notifications_v3(self):
        while True:
            try:
                listener = UserNotificationListener.current
                access = await listener.request_access_async()
                if access != 1:
                    log_throttled("notif_access", logging.WARNING, "Accès aux notifications Windows refusé (statut %s)", access, interval=300)
                    await asyncio.sleep(5)
                    continue

                seen_ids = set()
                first_pass = True

                while True:
                    is_retro = self.config.data.get("game_version", "Unity") == "Rétro"
                    is_autofocus_on = self.config.data.get("auto_focus_retro", False)
                    try:
                        notifs = await listener.get_notifications_async(NotificationKinds.TOAST)
                        current_ids = {n.id for n in notifs}

                        for n in notifs:
                            if n.id not in seen_ids:
                                seen_ids.add(n.id)
                                if not first_pass and is_retro and is_autofocus_on:
                                    try:
                                        binding = n.notification.visual.bindings[0]
                                        for t in binding.get_text_elements():
                                            if " - Dofus Retro" in t.text:
                                                pseudo = t.text.split(" - ")[0].strip()
                                                for index, acc in enumerate(self.logic.get_cycle_list()):
                                                    if acc['name'] == pseudo:
                                                        logger.info("Notification Rétro : focus sur %s", pseudo)
                                                        self.gui.root.after(0, self.logic.focus_window, acc['hwnd'])
                                                        self.current_idx = index
                                                        break
                                                break
                                    except Exception:
                                        log_exception_throttled("notif_parse", "Notification illisible")

                        seen_ids.intersection_update(current_ids)
                    except Exception:
                        log_exception_throttled("notif_poll", "Erreur pendant la lecture des notifications")

                    first_pass = False
                    await asyncio.sleep(0.3)
            except Exception:
                log_exception_throttled("notif_listener", "Erreur du moteur de notifications")
            await asyncio.sleep(3)
                

VERSION_URL = "https://raw.githubusercontent.com/LuframeCode/Dosoft/main/version.json"


def is_newer_version(candidate, current):
    """Return whether a dotted numeric candidate version is newer than current."""
    def parse(value):
        parts = str(value).split(".")
        if not parts or any(not part.isdigit() for part in parts):
            return None
        return tuple(int(part) for part in parts)

    candidate_parts = parse(candidate)
    current_parts = parse(current)
    if candidate_parts is None or current_parts is None:
        return False
    length = max(len(candidate_parts), len(current_parts))
    candidate_parts += (0,) * (length - len(candidate_parts))
    current_parts += (0,) * (length - len(current_parts))
    return candidate_parts > current_parts


def check_version(i18n=None):
    i18n = i18n or I18nManager("fr")
    try:
        response = requests.get(VERSION_URL, timeout=5)
        response.raise_for_status() 
        data = response.json()
        latest_version = data.get("version")
        newer = bool(latest_version and is_newer_version(latest_version, CURRENT_VERSION))
        logger.info("Vérification de version : locale %s, upstream %s, mise à jour : %s", CURRENT_VERSION, latest_version, newer)

        if newer:
            message = i18n.t(
                "version_update_required_text",
                "Une mise à jour est requise pour utiliser le logiciel.\n\nVotre version : {current_version}\nVersion disponible : {latest_version}\n\nMise à jour dispo sur Dosoft.fr"
            ).format(current_version=CURRENT_VERSION, latest_version=latest_version)
            ctypes.windll.user32.MessageBoxW(
                0,
                message,
                i18n.t("version_update_required_title", "Mise à jour requise"),
                0x10
            )
    except requests.RequestException:
        logger.warning("Vérification de version impossible", exc_info=True)
        ctypes.windll.user32.MessageBoxW(
            0,
            i18n.t("version_network_error_text", "Impossible de vérifier la version."),
            i18n.t("version_network_error_title", "Erreur réseau"),
            0x10
        )
    except Exception:
        logger.exception("Erreur inattendue pendant la vérification de version")
        raise


def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()
    except: return False

def run_as_admin():
    # skin/, sounds/, logo.ico et settings.json sont lus relativement au dossier de travail :
    # on le transmet explicitement au processus élevé.
    if getattr(sys, 'frozen', False):
        work_dir = os.getcwd()
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv[1:]), work_dir, 1)
    else:
        script = os.path.abspath(sys.argv[0])
        work_dir = os.path.dirname(script)
        params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}" {params}', work_dir, 1)
    logger.info("Relance en administrateur (dossier de travail : %s, code ShellExecute : %s)", work_dir, ret)

def handle_multiple_instances():
    i18n = I18nManager(Config().data.get("language", "fr"))
    mutex_name = "DOSOFT_SINGLE_INSTANCE_MUTEX"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        rep = messagebox.askyesno(i18n.t("header_instace_off", "Instance détectée"),i18n.t("popup_conflict_instance_text","Une instance de DOSOFT est déjà en cours d'exécution !\n\nVoulez-vous fermer l'ancienne instance pour ouvrir celle-ci ?"))
        if rep:
           
            hwnd = win32gui.FindWindow(None, APP_TITLE)
            if hwnd:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
                    ctypes.windll.kernel32.TerminateProcess(handle, 0)
                    ctypes.windll.kernel32.CloseHandle(handle)
                except: pass
            time.sleep(0.5) 
            root.destroy()
        else:
            root.destroy()
            sys.exit(0)
    return mutex

def start_application():
    setup_logging()
    logger.info(
        "Démarrage de %s (compilé : %s, admin : %s, Python %s, dossier : %s)",
        APP_TITLE, getattr(sys, 'frozen', False), bool(is_admin()), sys.version.split()[0], os.getcwd()
    )
    if not is_admin():
        run_as_admin()
        sys.exit() 
    cfg = Config()       
    _app_mutex = handle_multiple_instances()
    check_version(I18nManager(cfg.data.get("language", "fr")))

    app = OrganizerApp()
    app.gui.run()

if __name__ == "__main__":
    start_application()
