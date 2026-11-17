"""Select a stable local network adapter for student sign-in."""
import re
import subprocess
import sys
import uuid


def device_mac():
    if sys.platform == 'win32':
        script = (
            "$ProgressPreference='SilentlyContinue'; "
            "Get-NetAdapter -Physical -IncludeHidden -ErrorAction Stop | Sort-Object InterfaceGuid | "
            "ForEach-Object { if ($_.PermanentAddress) {$_.PermanentAddress} else {$_.MacAddress} }"
        )
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=8, check=True,
            )
            for line in result.stdout.decode('ascii', errors='ignore').splitlines():
                mac = re.sub('[:-]', '', line.strip()).lower()
                if re.fullmatch('[0-9a-f]{12}', mac) and int(mac, 16) and not int(mac[:2], 16) & 1:
                    return mac
        except (OSError, subprocess.SubprocessError):
            pass
    mac = uuid.getnode()
    if not mac or mac & (1 << 40):
        raise ValueError('未找到可用于学生登录的本机 MAC 地址。请检查网卡后重试。')
    return f'{mac:012x}'
