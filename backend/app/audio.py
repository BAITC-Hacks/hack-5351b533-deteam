"""Аудио: Silero VAD (onnx), сегментация речи, ресемплинг, WAV."""
import io, time, wave
from pathlib import Path
import numpy as np
import onnxruntime as ort

_SESS = None
def _sess():
    global _SESS
    if _SESS is None:
        o = ort.SessionOptions(); o.intra_op_num_threads = 1; o.inter_op_num_threads = 1
        _SESS = ort.InferenceSession(str(Path(__file__).parent / "assets" / "silero_vad.onnx"), sess_options=o, providers=["CPUExecutionProvider"])
    return _SESS

class Silero:
    CHUNK = 512
    def __init__(self):
        self.s = _sess(); self.reset()
    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32); self.ctx = np.zeros((1, 64), np.float32)
    def prob(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self.ctx, chunk[None, :]], axis=1).astype(np.float32)
        out, self.state = self.s.run(None, {"input": x, "state": self.state, "sr": np.array(16000, dtype=np.int64)})
        self.ctx = x[:, -64:]
        return float(out[0][0])

class Segmenter:
    """Режет входной PCM16 16 kHz на реплики. feed() возвращает события: ('start', t) и ('end', pcm_bytes, t_last_voice)."""
    def __init__(self, silence_ms=450, start_ms=96, min_speech_ms=250, preroll_ms=300, on=0.5, off=0.35, max_ms=30000):
        self.vad = Silero(); self.buf = b""; self.speaking = False
        self.silence_n = int(silence_ms / 32); self.start_n = max(1, int(start_ms / 32)); self.min_n = int(min_speech_ms / 32)
        self.pre_n = int(preroll_ms / 32); self.on, self.off = on, off; self.max_n = int(max_ms / 32)
        self.pre = []; self.seg = []; self.voiced_run = 0; self.silent_run = 0; self.voiced_total = 0; self.t_last_voice = None
    def reset(self):
        self.speaking = False; self.pre, self.seg = [], []; self.voiced_run = self.silent_run = self.voiced_total = 0; self.vad.reset()
    def force_end(self):
        if not self.speaking or not self.seg: return None
        pcm = b"".join(self.seg); t = self.t_last_voice or time.perf_counter(); self.reset()
        return ("end", pcm, t)
    def feed(self, data: bytes):
        self.buf += data; evs = []
        step = Silero.CHUNK * 2
        while len(self.buf) >= step:
            raw, self.buf = self.buf[:step], self.buf[step:]
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            p = self.vad.prob(x); now = time.perf_counter()
            if not self.speaking:
                self.pre.append(raw); self.pre = self.pre[-self.pre_n:]
                self.voiced_run = self.voiced_run + 1 if p >= self.on else 0
                if self.voiced_run >= self.start_n:
                    self.speaking = True; self.seg = list(self.pre); self.silent_run = 0; self.voiced_total = self.voiced_run; self.t_last_voice = now
                    evs.append(("start", now))
            else:
                self.seg.append(raw)
                if p >= self.off:
                    self.silent_run = 0
                    if p >= self.on: self.voiced_total += 1; self.t_last_voice = now
                else:
                    self.silent_run += 1
                if self.silent_run >= self.silence_n or len(self.seg) >= self.max_n:
                    pcm = b"".join(self.seg[: len(self.seg) - self.silent_run + 3]); t = self.t_last_voice
                    ok = self.voiced_total >= self.min_n
                    self.speaking = False; self.pre, self.seg = [], []; self.voiced_run = self.silent_run = 0
                    evs.append(("end", pcm, t) if ok else ("drop", now))
        return evs

def wav_bytes(pcm: bytes, sr=16000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    return b.getvalue()

def resample(pcm: bytes, sr_in: int, sr_out: int) -> bytes:
    if sr_in == sr_out or not pcm: return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if sr_in > sr_out and sr_in % sr_out == 0:
        k = sr_in // sr_out; n = len(x) // k * k
        y = x[:n].reshape(-1, k).mean(axis=1)
    else:
        n_out = int(len(x) * sr_out / sr_in)
        y = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()

KK_LETTERS = set("әіңғүұқөһӘІҢҒҮҰҚӨҺ")
def guess_lang(text: str) -> str:
    words = [w for w in text.split() if any(ch.isalpha() for ch in w)]
    if not words: return "ru"
    kk = sum(1 for w in words if set(w) & KK_LETTERS)
    share = kk / len(words)
    return "kk" if share >= 0.6 else "mixed" if kk else "ru"
