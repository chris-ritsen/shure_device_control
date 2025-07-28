import os
import socket

import sd_notify


class Notifier:
    def __init__(self):
        try:
            self._notifier = sd_notify.Notifier()
            self._enabled = self._notifier.enabled()
        except Exception:
            self._notifier = None
            self._enabled = False

    def status(self, message):
        if self._enabled:
            self._notifier.status(message)
        else:
            print(f"[status] {message}")

    def ready(self):
        if self._enabled:
            self._notifier.ready()
        else:
            print("[ready]")

    def stopping(self):
        if self._enabled:
            try:
                self._notifier.notify_stopping()
            except AttributeError:
                if "NOTIFY_SOCKET" in os.environ:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                        s.connect(os.environ["NOTIFY_SOCKET"])
                        s.sendall(b"STOPPING=1\n")
        else:
            print("[stopping]")
