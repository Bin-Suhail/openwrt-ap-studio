"""In-app backend. Nothing connects to a router until Read is pressed."""
from pathlib import Path
import threading
import core
import server

_instance = None

def start(files_dir):
    global _instance
    if _instance is not None:
        return server.ORIGIN
    from java import jclass
    transport = jclass('net.openwrt.apstudio.RouterTransport')
    def send(url, body, timeout):
        try:
            return str(transport.post(url, body, int(timeout * 1000)))
        except Exception:
            raise core.RouterConnectionError('تعذر الوصول للمودم عبر الاتصال المختار. تحقق من الواي فاي/الإيثرنت وعنوان الجوال والمودم.') from None
    core.TRANSPORT = send
    server.ROOT = Path(files_dir)
    server.ANDROID = True
    _instance = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    server.ORIGIN = f'http://127.0.0.1:{_instance.server_port}'
    threading.Thread(target=_instance.serve_forever, daemon=True).start()
    return server.ORIGIN

def active():
    return server.ACTIVE is not None

def export_backups(files_dir):
    import zipfile
    root = Path(files_dir)
    target = root / 'APStudio-backups.zip'
    backup = root / 'backups'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        if backup.is_dir():
            for file in sorted(backup.rglob('*.json')):
                archive.write(file, 'backups/' + str(file.relative_to(backup)))
        archive.writestr('README.txt', 'OpenWrt configuration backups. These may contain existing Wi-Fi keys. Keep this archive private.\n')
    return str(target)
