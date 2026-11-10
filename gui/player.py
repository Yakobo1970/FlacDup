"""Wiedergabe: Markerbereich in Schleife oder ab einer Stelle bis zum Trackende (sounddevice).

Beim Abspielen bis zum Ende wird in Stuecken nachgeladen, damit auch stundenlange Mixe
nicht komplett in den Speicher muessen.
"""
from __future__ import annotations

import threading
from collections import deque
from math import gcd

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QTimer, Signal

from engine.decode import read_segment

CHUNK_S = 20.0      # Nachladeblock beim Abspielen bis zum Ende
AHEAD_CHUNKS = 2    # so viele Bloecke im Voraus bereithalten
SEAM_FADE_S = 0.005  # Blende an Spruengen ueber entfernte Bereiche (Editor-Vorschau)


def _pieces(ranges: list[tuple[float, float]]) -> list[tuple[float, float, bool, bool]]:
    """Bereiche in Nachladebloecke zerlegen: (von, bis, Blende ein, Blende aus).
    Geblendet wird nur an echten Spruengen zwischen zwei Bereichen."""
    out = []
    for k, (a, b) in enumerate(ranges):
        t = a
        while t < b - 1e-6:
            e = min(b, t + CHUNK_S)
            out.append((t, e, k > 0 and t == a, k < len(ranges) - 1 and e >= b - 1e-6))
            t = e
    return out


def _fade(data: np.ndarray, sr: int, fade_in: bool, fade_out: bool) -> np.ndarray:
    n = min(int(SEAM_FADE_S * sr), data.shape[0] // 4)
    if n <= 0 or not (fade_in or fade_out):
        return data
    data = data.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    if fade_in:
        data[:n] *= ramp
    if fade_out:
        data[-n:] *= ramp[::-1]
    return data


class LoopPlayer(QObject):
    position_changed = Signal(int, float)   # file_id, Sekunde im Track
    stopped = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._chunks: deque[tuple[np.ndarray, float]] = deque()   # (Audio, Startsekunde im Track)
        self._cur: np.ndarray | None = None
        self._cur_t = 0.0         # Startsekunde des laufenden Blocks im Track
        self._cur_pos = 0
        self._played = 0          # abgespielte Frames seit t0 (fuer die Positionsanzeige)
        self._eof = False
        self._loader: threading.Thread | None = None
        self._cancel = threading.Event()
        self.file_id: int | None = None
        self.marker_id: int | None = None
        self.t0 = 0.0
        self.sr = 44100
        self.out_sr = 44100
        self.loop = True
        self._loop_len = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    @property
    def playing(self) -> bool:
        return self._stream is not None

    # ------------------------------------------------------------ Start
    def play(self, file_id: int, path: str, t0: float, t1: float, marker_id: int | None = None,
             loop: bool = True, ranges: list[tuple[float, float]] | None = None):
        """loop=True: [t0, t1) in Schleife. loop=False: ab t0 bis t1 (Trackende), nachladend.
        ranges (nur ohne Schleife): diese Bereiche nacheinander spielen, alles dazwischen
        ueberspringen (Editor-Vorschau ohne die entfernten Stellen)."""
        self.stop()
        if loop:
            pieces = [(t0, t1, False, False)]
        else:
            pieces = _pieces(ranges if ranges is not None else [(t0, t1)])
            if not pieces:
                self.error.emit("Ausschnitt ist leer")
                return
            t0 = pieces[0][0]
        first_end = pieces[0][1]
        try:
            data, sr = read_segment(path, t0, first_end)
        except Exception as exc:
            self.error.emit(f"Ausschnitt nicht lesbar: {exc}")
            return
        if data.shape[0] == 0:
            self.error.emit("Ausschnitt ist leer")
            return
        data = data[:, :2]
        if not loop:
            data = _fade(data, sr, pieces[0][2], pieces[0][3])
        if loop:  # kurze Blenden an den Schleifenkanten, damit die Naht nicht selbst knackt
            fade = min(int(0.004 * sr), data.shape[0] // 4)
            if fade > 0:
                ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
                data = data.copy()
                data[:fade] *= ramp
                data[-fade:] *= ramp[::-1]
        self.file_id, self.marker_id = file_id, marker_id
        self.t0, self.sr, self.loop = t0, sr, loop
        self._chunks.clear()
        self._cur, self._cur_t, self._cur_pos, self._played = None, t0, 0, 0
        self._eof = loop or len(pieces) == 1
        self._cancel = threading.Event()
        if not self._open_stream(sr, data.shape[1]):
            return
        first = self._convert(data)
        self._loop_len = first.shape[0]
        self._chunks.append((first, t0))
        if not loop and not self._eof:
            self._loader = threading.Thread(target=self._load_rest, args=(path, pieces[1:], data.shape[1]),
                                            daemon=True)
            self._loader.start()
        self._stream.start()
        self._timer.start()

    def _open_stream(self, sr: int, channels: int) -> bool:
        for out_sr in (sr, 48000):
            try:
                # latency="high": groesserer Puffer, damit kurze Haenger der Oberflaeche nicht hoerbar werden
                self._stream = sd.OutputStream(samplerate=out_sr, channels=channels, dtype="float32",
                                               callback=self._callback, latency="high")
                self.out_sr = out_sr
                return True
            except Exception as exc:
                last = exc
        self._stream = None
        self.error.emit(f"Audioausgabe nicht möglich: {last}")
        return False

    def _convert(self, data: np.ndarray) -> np.ndarray:
        """Auf die Geraete-Abtastrate umrechnen, falls das Geraet die Originalrate nicht kann."""
        if self.out_sr != self.sr:
            from scipy.signal import resample_poly
            g = gcd(self.out_sr, self.sr)
            data = resample_poly(data, self.out_sr // g, self.sr // g, axis=0)
        return np.ascontiguousarray(data, dtype=np.float32)

    def _load_rest(self, path: str, pieces: list, channels: int):
        """Hintergrund: Track stueckweise nachladen, immer nur ein paar Bloecke voraus."""
        cancel = self._cancel
        for a, b, fade_in, fade_out in pieces:
            while not cancel.is_set():
                with self._lock:
                    queued = len(self._chunks)
                if queued < AHEAD_CHUNKS:
                    break
                cancel.wait(0.2)
            if cancel.is_set():
                return
            try:
                data, sr = read_segment(path, a, b)
            except Exception:
                break
            if data.shape[0] == 0:
                continue
            chunk = self._convert(_fade(data[:, :channels], sr, fade_in, fade_out))
            with self._lock:
                if cancel.is_set():
                    return
                self._chunks.append((chunk, a))
        with self._lock:
            self._eof = True

    # ------------------------------------------------------------ Audio-Callback
    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            written = 0
            while written < frames:
                if self._cur is None or self._cur_pos >= self._cur.shape[0]:
                    if self.loop and self._cur is not None:
                        self._cur_pos = 0          # Schleife: denselben Block von vorn
                        self._played = 0
                    elif self._chunks:
                        (self._cur, self._cur_t), self._cur_pos = self._chunks.popleft(), 0
                    elif self._eof:
                        outdata[written:].fill(0)
                        raise sd.CallbackStop
                    else:                          # Nachladen hinkt hinterher: kurz Stille
                        outdata[written:].fill(0)
                        return
                take = min(frames - written, self._cur.shape[0] - self._cur_pos)
                outdata[written:written + take] = self._cur[self._cur_pos:self._cur_pos + take]
                self._cur_pos += take
                self._played += take
                written += take

    # ------------------------------------------------------------ Anzeige / Stopp
    def _tick(self):
        if self._stream is None:
            return
        if not self._stream.active:
            self.stop()
            return
        if self.loop:
            pos = self.t0 + self._played / self.out_sr
        else:  # Position aus dem laufenden Block: springt mit ueber entfernte Bereiche
            pos = self._cur_t + self._cur_pos / self.out_sr
        self.position_changed.emit(self.file_id or -1, pos)

    def stop(self):
        self._timer.stop()
        self._cancel.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            with self._lock:
                self._chunks.clear()
                self._cur = None
            fid = self.file_id
            self.file_id = self.marker_id = None
            if fid is not None:
                self.position_changed.emit(fid, -1.0)
            self.stopped.emit()
