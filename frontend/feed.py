"""Status-feed subscriber: a QTcpSocket on the localhost feed, framed by frontend.decode
and pushed into the OverlayModel.

The daemon is the always-up server and the overlay is the client that comes and goes,
so this reconnects quietly forever — starting the overlay before the daemon,
or restarting the daemon under it, both just work.
"""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from frontend.decode import HOST, PORT, Decoder

log = logging.getLogger("nothal.teleprompter")

RECONNECT_MS = 1000
MIC_IDLE_MS = 250   # no mic frame for this long -> bars fall. The truthful indicator
                    # is implemented by construction here: the bars follow live 'mic'
                    # messages, never an inferred state (so a mute upstream drops them).


class Feed(QObject):
    def __init__(self, model, host: str = HOST, port: int = PORT) -> None:
        super().__init__()
        self._model = model
        self._host, self._port = host, port
        self._dec = Decoder()

        self._sock = QTcpSocket(self)
        self._sock.readyRead.connect(self._on_ready)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_closed)
        self._sock.errorOccurred.connect(lambda _err: self._on_closed())

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._connect)

        self._mic_idle = QTimer(self)
        self._mic_idle.setSingleShot(True)
        self._mic_idle.setInterval(MIC_IDLE_MS)
        self._mic_idle.timeout.connect(lambda: self._model.set_mic(0.0))

        self._connect()

    def _connect(self) -> None:
        if self._sock.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._sock.connectToHost(self._host, self._port)

    def _on_connected(self) -> None:
        # A remnant left in the decoder by a connection that died mid-line must not glue onto
        # this stream's first message — which, with the snapshot on connect, is the
        # state message that tells the island what the daemon is doing.
        self._dec.reset()
        log.info("feed connected (%s:%d)", self._host, self._port)

    def _on_ready(self) -> None:
        for msg in self._dec.feed(bytes(self._sock.readAll().data())):
            self._model.apply(msg)
            if msg["type"] == "mic":
                self._mic_idle.start()

    def send(self, msg: dict) -> None:
        """One upstream feed line, today only `dismiss`. Best-effort on purpose:
        if the daemon is not there, there is no turn to cancel, and the island has already
        hidden itself without waiting for anyone's permission."""
        if self._sock.state() != QAbstractSocket.SocketState.ConnectedState:
            log.info("not connected — nothing upstream to tell")
            return
        self._sock.write((json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8"))

    def _on_closed(self) -> None:
        # Deaf -> the island goes away rather than freezing on a stale frame. `idle` no longer
        # clears anything (the answer must outlive it to be read), so losing the feed has to
        # say so explicitly: we know nothing, show nothing.
        self._model.feed_lost()
        self._sock.abort()
        if not self._retry.isActive():
            self._retry.start(RECONNECT_MS)
