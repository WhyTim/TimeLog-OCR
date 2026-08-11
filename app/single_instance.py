from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "TimeLogOCR.TimeLogOCR"


@dataclass(slots=True)
class SingleInstanceClient:
    name: str = SERVER_NAME
    timeout_ms: int = 500

    def notify_existing(self, message: str = "activate") -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(self.timeout_ms):
            return False
        socket.write(message.encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(self.timeout_ms)
        socket.disconnectFromServer()
        return True


class SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, name: str = SERVER_NAME, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.server: QLocalServer | None = None
        lock_name = "".join(character if character.isalnum() else "_" for character in name)
        self.lock_file = QLockFile(str(Path(QDir.tempPath()) / f"{lock_name}.lock"))
        self.lock_file.setStaleLockTime(0)
        self._lock_acquired = False

    def acquire(self) -> bool:
        if not self.lock_file.tryLock(0):
            if not self.lock_file.removeStaleLockFile() or not self.lock_file.tryLock(0):
                return False
        self._lock_acquired = True
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._handle_connection)
        if self.server.listen(self.name):
            return True
        # The lock is ours, so a remaining endpoint can only be stale.
        QLocalServer.removeServer(self.name)
        if self.server.listen(self.name):
            return True
        self.server.close()
        self.server = None
        self.lock_file.unlock()
        self._lock_acquired = False
        return False

    def _handle_connection(self) -> None:
        if not self.server:
            return
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: s.readAll())
            socket.disconnected.connect(socket.deleteLater)
            socket.disconnectFromServer()
        self.activate_requested.emit()

    def release(self) -> None:
        if self.server:
            self.server.close()
            QLocalServer.removeServer(self.name)
            self.server = None
        if self._lock_acquired:
            self.lock_file.unlock()
            self._lock_acquired = False
