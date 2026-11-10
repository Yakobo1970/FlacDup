"""A/B-Wiedergabe: beide Dateien liegen dekodiert im Speicher, umgeschaltet wird an der
entsprechenden Stelle der anderen Datei -- mit Feinabgleich auf das Sample und 5 ms Blende,
so dass es klingt wie ein durchgehender Track."""
from __future__ import annotations

import threading
from math import gcd

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QTimer, Signal
from scipy.signal import resample_poly

from engine import compare
from engine.decode import DecodeReport, iter_blocks

FADE_S = 0.005
ALIGN_WIN_S = 0.40      # so viel Audio wird beim Umschalten verglichen
ALIGN_RANGE_S = 0.060   # ... in diesem Suchbereich um die vorhergesagte Stelle


def _decode_stereo(path: str) -> tuple[np.ndarray, int]:
    rep = DecodeReport()
    parts, sr = [], 44100
    for block, sr in iter_blocks(path, rep):
        if block.shape[1] == 1:
            block = np.repeat(block, 2, axis=1)
        parts.append(block)
    if not parts:
        return np.zeros((0, 2), np.float32), sr
    return np.ascontiguousarray(np.concatenate(parts), dtype=np.float32), sr


def _envelope(x: np.ndarray, points: int = 4000) -> np.ndarray:
    if x.shape[0] == 0:
        return np.zeros(points, np.float32)
    m = np.abs(x).max(axis=1)
    edges = np.linspace(0, m.size, points + 1).astype(np.int64)
    peak = np.maximum.reduceat(m, np.minimum(edges[:-1], m.size - 1))
    return (peak / max(1e-6, float(peak.max()))).astype(np.float32)


class ABPlayer(QObject):
    loaded = Signal(int)                  # pair_id
    load_failed = Signal(int, str)
    position_changed = Signal(int, float)  # Seite (0 = A, 1 = B), Sekunde in dieser Datei
    side_changed = Signal(int)
    state_changed = Signal(bool)          # spielt?
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pair_id: int | None = None
        self.buf: list[np.ndarray] = [np.zeros((0, 2), np.float32)] * 2
        self.env: list[np.ndarray] = [np.zeros(0, np.float32)] * 2
        self.dur = [0.0, 0.0]
        self.sr = 44100
        self.res: dict | None = None
        self.ts, self.off = 1.0, 0.0
        self.side = 0
        self.pos = [0, 0]                 # Abspielposition je Seite (Samples)
        self._lock = threading.Lock()
        self._fade: tuple[int, int] | None = None   # (alte Seite, alte Position) waehrend der Blende
        self._fade_done = 0
        self._stream: sd.OutputStream | None = None
        self._load_token = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ Laden
    def load(self, pair_id: int, path_a: str, path_b: str, ts: float, off: float):
        self.stop()
        self._load_token += 1
        token = self._load_token
        self.pair_id = None

        def work():
            try:
                a, sra = _decode_stereo(path_a)
                b, srb = _decode_stereo(path_b)
                if srb != sra and b.shape[0]:
                    g = gcd(sra, srb)
                    b = resample_poly(b, sra // g, srb // g, axis=0).astype(np.float32)
                env = [_envelope(a), _envelope(b)]
            except Exception as exc:
                if token == self._load_token:
                    self.load_failed.emit(pair_id, str(exc))
                return
            if token != self._load_token:
                return
            with self._lock:
                self.buf = [a, b]
                self.env = env
                self.sr = sra
                self.dur = [a.shape[0] / sra, b.shape[0] / sra]
                self.ts, self.off = ts or 1.0, off or 0.0
                self.pos = [0, 0]
                self.side = 0
                self.pair_id = pair_id
            self.loaded.emit(pair_id)

        threading.Thread(target=work, daemon=True).start()

    def set_compare(self, res: dict | None):
        self.res = res

    # ------------------------------------------------------------ Zeitabbildung
    def map_time(self, t: float, from_side: int) -> float:
        """Stelle in der anderen Datei, die t in from_side entspricht."""
        mapped = compare.map_time(self.res, t, from_side == 0) if self.res is not None else None
        if mapped is None:
            mapped = t * self.ts + self.off if from_side == 0 else (t - self.off) / self.ts
        return float(np.clip(mapped, 0.0, self.dur[1 - from_side]))

    def _refine(self, src: int, ps: int, dst: int, pd: int) -> int:
        """Vorhergesagte Stelle pd in dst per Kreuzkorrelation auf das Sample genau setzen."""
        if abs(self.ts - 1.0) > 1e-3:
            return pd
        w, r = int(ALIGN_WIN_S * self.sr), int(ALIGN_RANGE_S * self.sr)
        a = self.buf[src][ps:ps + w].mean(axis=1)
        lo = pd - r
        b = self.buf[dst][max(0, lo):pd + w + r].mean(axis=1)
        if a.size < w // 2 or b.size < a.size + 2 or np.abs(a).max() < 1e-4:
            return pd
        n = 1 << int(np.ceil(np.log2(a.size + b.size)))
        corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)[:b.size - a.size + 1]
        # normieren, damit laute Stellen nicht bevorzugt werden
        c2 = np.concatenate([[0.0], np.cumsum(b.astype(np.float64) ** 2)])
        energy = np.sqrt((c2[a.size:a.size + corr.size] - c2[:corr.size]) * float(a @ a)) + 1e-9
        score = corr / energy
        k = int(np.argmax(score))
        if score[k] < 0.5:      # kein klares Maximum (anders gemischt, Rauschen): Vorhersage behalten
            return pd
        return max(0, lo) + k

    # ------------------------------------------------------------ Steuerung
    @property
    def playing(self) -> bool:
        return self._stream is not None

    def position(self, side: int | None = None) -> float:
        side = self.side if side is None else side
        return self.pos[side] / self.sr

    def seek(self, side: int, t: float):
        with self._lock:
            n = self.buf[side].shape[0]
            self.pos[side] = int(np.clip(t * self.sr, 0, max(0, n - 1)))
            if side != self.side:
                self._fade = None
            self.side = side
        self.side_changed.emit(side)
        self.position_changed.emit(side, self.position(side))

    def switch(self, to_side: int | None = None):
        """Auf die andere Datei an der entsprechenden Stelle umschalten."""
        if self.pair_id is None:
            return
        src = self.side
        dst = 1 - src if to_side is None else to_side
        if dst == src:
            return
        t = self.pos[src] / self.sr
        pd = int(self.map_time(t, src) * self.sr)
        with self._lock:
            pd = self._refine(src, self.pos[src], dst, pd)
            pd = min(pd, max(0, self.buf[dst].shape[0] - 1))
            if self.playing:
                self._fade, self._fade_done = (src, self.pos[src]), 0
            self.pos[dst] = pd
            self.side = dst
        self.side_changed.emit(dst)
        self.position_changed.emit(dst, self.position(dst))

    def play(self):
        if self.pair_id is None or self.playing:
            return
        if self.pos[self.side] >= self.buf[self.side].shape[0] - 1:
            self.pos[self.side] = 0
        try:
            self._stream = sd.OutputStream(samplerate=self.sr, channels=2, dtype="float32",
                                           callback=self._callback, latency="high")
        except Exception:
            try:
                self._stream = sd.OutputStream(samplerate=48000, channels=2, dtype="float32",
                                               callback=self._callback, latency="high")
                self._resample_all(48000)
            except Exception as exc:
                self._stream = None
                self.error.emit(f"Audioausgabe nicht möglich: {exc}")
                return
        self._stream.start()
        self._timer.start()
        self.state_changed.emit(True)

    def _resample_all(self, out_sr: int):
        """Geraet kann die Abtastrate nicht: beide Puffer umrechnen (Positionen mitnehmen)."""
        g = gcd(out_sr, self.sr)
        with self._lock:
            self.buf = [resample_poly(b, out_sr // g, self.sr // g, axis=0).astype(np.float32) for b in self.buf]
            self.pos = [int(p * out_sr / self.sr) for p in self.pos]
            self.sr = out_sr

    def pause(self):
        self._timer.stop()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            self.state_changed.emit(False)
        self.position_changed.emit(self.side, self.position())

    def stop(self):
        self.pause()

    def toggle(self):
        self.pause() if self.playing else self.play()

    # ------------------------------------------------------------ Audio-Callback
    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            s = self.side
            buf, p = self.buf[s], self.pos[s]
            take = max(0, min(frames, buf.shape[0] - p))
            outdata[:take] = buf[p:p + take]
            outdata[take:] = 0
            self.pos[s] = p + take
            if self._fade is not None:
                fs, fp = self._fade
                fade_len = max(1, int(FADE_S * self.sr))
                old = self.buf[fs]
                n = min(frames, fade_len - self._fade_done, max(0, old.shape[0] - fp))
                if n > 0:
                    k = np.arange(self._fade_done, self._fade_done + n, dtype=np.float32) / fade_len
                    outdata[:n] = outdata[:n] * k[:, None] + old[fp:fp + n] * (1.0 - k)[:, None]
                self._fade_done += n
                self._fade = None if self._fade_done >= fade_len or n <= 0 else (fs, fp + n)
                if self._fade is not None:
                    self.pos[fs] = fp + n
            if take < frames:
                raise sd.CallbackStop

    def _tick(self):
        if self._stream is None:
            return
        if not self._stream.active:
            self.pause()
            return
        self.position_changed.emit(self.side, self.position())
