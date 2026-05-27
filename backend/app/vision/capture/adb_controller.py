# backend/app/vision/capture/adb_controller.py
"""
AEGIS AI - ADB Controller
Android Debug Bridge wrapper for controlling spectator devices.
Handles device commands, BGMI automation, and screen interaction.
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.vision.adb")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ADBCommand(str, Enum):
    """Common ADB commands"""
    DEVICES = "devices"
    SHELL = "shell"
    PULL = "pull"
    PUSH = "push"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    REBOOT = "reboot"
    SCREENSHOT = "screencap"
    SCREENRECORD = "screenrecord"
    INPUT_TAP = "input tap"
    INPUT_SWIPE = "input swipe"
    INPUT_TEXT = "input text"
    INPUT_KEYEVENT = "input keyevent"
    GET_PROP = "getprop"
    SET_PROP = "setprop"
    AM_START = "am start"
    AM_FORCE_STOP = "am force-stop"
    PM_LIST = "pm list packages"
    WM_SIZE = "wm size"
    WM_DENSITY = "wm density"
    DUMPSYS_BATTERY = "dumpsys battery"
    DUMPSYS_ACTIVITY = "dumpsys activity"

@dataclass
class ADBDevice:
    """ADB device information"""
    serial: str
    model: str = ""
    android_version: str = ""
    sdk_version: str = ""
    state: str = "device"
    connection_type: str = "usb"
    ip_address: str = ""
    port: int = 5555


class ADBController:
    """
    Android Debug Bridge controller for spectator devices.
    
    Features:
    - Device discovery and connection
    - BGMI automation (launch, login, join room)
    - Screen interaction (tap, swipe, text)
    - Screen recording and screenshot
    - Device settings management
    - Performance monitoring
    - Error recovery
    """
    
    def __init__(self, adb_path: str = "adb"):
        self.adb_path = adb_path
        self.connected_devices: Dict[str, ADBDevice] = {}
        self._log = logger.bind(component="adb_controller")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE DISCOVERY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def discover_devices(self) -> List[ADBDevice]:
        """Discover all connected ADB devices"""
        
        result = await self._run_adb("devices -l")
        devices = []
        
        for line in result.split('\n')[1:]:
            if line.strip() and 'device' in line:
                parts = line.split()
                serial = parts[0]
                
                # Get device info
                model = await self._get_prop(serial, "ro.product.model")
                android_ver = await self._get_prop(serial, "ro.build.version.release")
                sdk = await self._get_prop(serial, "ro.build.version.sdk")
                
                device = ADBDevice(
                    serial=serial,
                    model=model.strip() if model else "Unknown",
                    android_version=android_ver.strip() if android_ver else "Unknown",
                    sdk_version=sdk.strip() if sdk else "0",
                )
                
                devices.append(device)
                self.connected_devices[serial] = device
                
                self._log.info(
                    f"Device found: {model} (Android {android_ver})",
                    serial=serial,
                )
        
        return devices
    
    async def connect_device(self, ip: str, port: int = 5555) -> bool:
        """Connect to device over TCP/IP"""
        result = await self._run_adb(f"connect {ip}:{port}")
        return "connected" in result.lower()
    
    async def disconnect_device(self, serial: str) -> bool:
        """Disconnect from device"""
        await self._run_adb(f"-s {serial} disconnect")
        self.connected_devices.pop(serial, None)
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BGMI AUTOMATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def launch_bgmi(self, serial: str) -> bool:
        """Launch BGMI on device"""
        
        # Check if BGMI is installed
        result = await self._run_adb(f"-s {serial} shell pm list packages com.pubg.imobile")
        
        if "com.pubg.imobile" not in result:
            self._log.error(f"BGMI not installed on {serial}")
            return False
        
        # Force stop first (clean start)
        await self._run_adb(f"-s {serial} shell am force-stop com.pubg.imobile")
        await asyncio.sleep(2)
        
        # Launch BGMI
        await self._run_adb(
            f"-s {serial} shell monkey -p com.pubg.imobile "
            f"-c android.intent.category.LAUNCHER 1"
        )
        
        # Wait for launch
        await asyncio.sleep(10)
        
        self._log.info(f"BGMI launched on {serial}")
        return True
    
    async def stop_bgmi(self, serial: str) -> bool:
        """Force stop BGMI"""
        await self._run_adb(f"-s {serial} shell am force-stop com.pubg.imobile")
        return True
    
    async def is_bgmi_running(self, serial: str) -> bool:
        """Check if BGMI is running"""
        result = await self._run_adb(f"-s {serial} shell pidof com.pubg.imobile")
        return bool(result.strip())
    
    async def join_custom_room(
        self,
        serial: str,
        room_id: str,
        password: str,
        spectator_slot: int = 0,
    ) -> bool:
        """Automate joining a BGMI custom room"""
        
        self._log.info(f"Joining custom room on {serial}: {room_id}")
        
        # Navigate to custom room screen
        # This requires screen coordinate mapping for BGMI UI
        
        # Step 1: Tap "Room" button (coordinates depend on device)
        await self.tap(serial, 540, 1600)
        await asyncio.sleep(2)
        
        # Step 2: Enter room ID
        await self.tap(serial, 540, 800)
        await asyncio.sleep(1)
        await self.input_text(serial, room_id)
        
        # Step 3: Enter password
        await self.tap(serial, 540, 1000)
        await asyncio.sleep(1)
        await self.input_text(serial, password)
        
        # Step 4: Join
        await self.tap(serial, 540, 1200)
        await asyncio.sleep(5)
        
        self._log.info(f"Joined custom room on {serial}")
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCREEN INTERACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def tap(self, serial: str, x: int, y: int) -> bool:
        """Tap on screen at coordinates"""
        await self._run_adb(f"-s {serial} shell input tap {x} {y}")
        return True
    
    async def swipe(
        self,
        serial: str,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """Swipe on screen"""
        await self._run_adb(
            f"-s {serial} shell input swipe {x1} {y1} {x2} {y2} {duration_ms}"
        )
        return True
    
    async def input_text(self, serial: str, text: str) -> bool:
        """Input text"""
        # Escape spaces for ADB
        escaped_text = text.replace(" ", "%s")
        await self._run_adb(f'-s {serial} shell input text "{escaped_text}"')
        return True
    
    async def press_key(self, serial: str, keycode: int) -> bool:
        """Press a key by keycode"""
        await self._run_adb(f"-s {serial} shell input keyevent {keycode}")
        return True
    
    async def press_back(self, serial: str) -> bool:
        """Press back button"""
        return await self.press_key(serial, 4)
    
    async def press_home(self, serial: str) -> bool:
        """Press home button"""
        return await self.press_key(serial, 3)
    
    async def press_recent(self, serial: str) -> bool:
        """Press recent apps"""
        return await self.press_key(serial, 187)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCREEN CAPTURE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def screenshot(self, serial: str, output_path: str = "/sdcard/screenshot.png") -> Optional[bytes]:
        """Take a screenshot"""
        await self._run_adb(f"-s {serial} shell screencap -p {output_path}")
        result = await self._run_adb(f"-s {serial} pull {output_path}")
        
        # Read the pulled file
        try:
            with open("screenshot.png", "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
    
    async def start_screen_record(
        self,
        serial: str,
        output_path: str = "/sdcard/record.mp4",
        duration: int = 1800,
        bitrate: int = 8000000,
        size: str = "1920x1080",
    ) -> bool:
        """Start screen recording"""
        cmd = (
            f"-s {serial} shell screenrecord "
            f"--time-limit {duration} "
            f"--bit-rate {bitrate} "
            f"--size {size} "
            f"{output_path}"
        )
        # Run in background
        asyncio.create_task(self._run_adb(cmd))
        return True
    
    async def stop_screen_record(self, serial: str) -> bool:
        """Stop screen recording"""
        await self._run_adb(f"-s {serial} shell pkill -l 2 screenrecord")
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE SETTINGS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def set_screen_timeout(self, serial: str, timeout_ms: int = 1800000) -> bool:
        """Set screen timeout (30 min default)"""
        await self._run_adb(
            f"-s {serial} shell settings put system screen_off_timeout {timeout_ms}"
        )
        return True
    
    async def set_brightness(self, serial: str, level: int = 255) -> bool:
        """Set screen brightness (0-255)"""
        await self._run_adb(
            f"-s {serial} shell settings put system screen_brightness {level}"
        )
        return True
    
    async def disable_animations(self, serial: str) -> bool:
        """Disable animations for better performance"""
        settings = [
            ("global", "window_animation_scale", "0.0"),
            ("global", "transition_animation_scale", "0.0"),
            ("global", "animator_duration_scale", "0.0"),
        ]
        
        for namespace, key, value in settings:
            await self._run_adb(
                f"-s {serial} shell settings put {namespace} {key} {value}"
            )
        
        return True
    
    async def set_resolution(self, serial: str, width: int, height: int) -> bool:
        """Set screen resolution"""
        await self._run_adb(f"-s {serial} shell wm size {width}x{height}")
        return True
    
    async def set_density(self, serial: str, density: int) -> bool:
        """Set screen density"""
        await self._run_adb(f"-s {serial} shell wm density {density}")
        return True
    
    async def reset_resolution(self, serial: str) -> bool:
        """Reset to default resolution"""
        await self._run_adb(f"-s {serial} shell wm size reset")
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE INFO
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def get_battery_info(self, serial: str) -> Dict[str, Any]:
        """Get battery information"""
        result = await self._run_adb(f"-s {serial} shell dumpsys battery")
        
        info = {}
        for line in result.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                info[key.strip().lower()] = value.strip()
        
        return {
            "level": int(info.get("level", 0)),
            "temperature": float(info.get("temperature", 25)) / 10,
            "voltage": int(info.get("voltage", 0)),
            "health": info.get("health", "unknown"),
            "status": info.get("status", "unknown"),
            "technology": info.get("technology", "unknown"),
        }
    
    async def get_memory_info(self, serial: str) -> Dict[str, Any]:
        """Get memory information"""
        result = await self._run_adb(f"-s {serial} shell dumpsys meminfo")
        
        # Parse relevant lines
        info = {"total_mb": 0, "used_mb": 0, "free_mb": 0}
        
        for line in result.split('\n'):
            if "Total RAM" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if 'K' in part or 'M' in part or 'G' in part:
                        info["total_mb"] = self._parse_memory(part)
            elif "Free RAM" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if 'K' in part or 'M' in part or 'G' in part:
                        info["free_mb"] = self._parse_memory(part)
        
        info["used_mb"] = info["total_mb"] - info["free_mb"]
        return info
    
    def _parse_memory(self, value: str) -> float:
        """Parse memory value to MB"""
        value = value.replace(',', '')
        if 'G' in value:
            return float(value.replace('G', '')) * 1024
        elif 'M' in value:
            return float(value.replace('M', ''))
        elif 'K' in value:
            return float(value.replace('K', '')) / 1024
        return 0.0
    
    async def _get_prop(self, serial: str, prop: str) -> str:
        """Get device property"""
        result = await self._run_adb(f"-s {serial} shell getprop {prop}")
        return result.strip()
    
    async def _run_adb(self, command: str) -> str:
        """Run an ADB command"""
        try:
            cmd = f"{self.adb_path} {command}"
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0 and stderr:
                self._log.debug(f"ADB stderr: {stderr.decode()}")
            
            return stdout.decode()
            
        except Exception as e:
            self._log.error(f"ADB command failed: {command} - {e}")
            return ""
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE RECOVERY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def restart_device(self, serial: str) -> bool:
        """Restart a device"""
        await self._run_adb(f"-s {serial} reboot")
        self._log.info(f"Device restarting: {serial}")
        return True
    
    async def restart_bgmi(self, serial: str) -> bool:
        """Restart BGMI on device"""
        await self.stop_bgmi(serial)
        await asyncio.sleep(3)
        return await self.launch_bgmi(serial)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get ADB controller statistics"""
        return {
            "connected_devices": len(self.connected_devices),
            "devices": {
                serial: {
                    "model": d.model,
                    "android": d.android_version,
                    "state": d.state,
                }
                for serial, d in self.connected_devices.items()
            },
        }
    
    def reset(self) -> None:
        """Reset ADB controller"""
        self.connected_devices.clear()
        self._log.info("ADB controller reset")