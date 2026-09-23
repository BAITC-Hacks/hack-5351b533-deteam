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
    """Режет входной PCM16 16 kHz на реплики по Silero VAD (окно 32 мс).
    feed() возвращает события:
      ('start', t)                  речь подтверждена (>= min_speech_ms озвученных окон), можно перебивать бота;
      ('pause', pcm, t_last_voice)  тишина early_ms внутри реплики: можно начать STT заранее (спекулятивно);
      ('end', pcm, t_last_voice, voiced_ms)  реплика закончилась (silence_ms тишины), pcm с pre-roll и хвостом ~100 мс;
      ('drop', t)                   короткий всплеск (кашель, щелчок) без подтверждения, игнорируется.
    Окно озвучено, если p >= on и RMS >= min_rms (защита от ложных стартов на тихом шуме)."""
    W = 32  # мс на окно Silero при 16 kHz
    def __init__(self, silence_ms=450, onset_ms=64, min_speech_ms=160, preroll_ms=400, on=0.5, off=0.35, max_ms=20000, min_rms=0.002, early_ms=200):
        self.vad = Silero(); self.buf = b""; W = self.W
        self.silence_n = max(2, round(silence_ms / W)); self.onset_n = max(1, round(onset_ms / W)); self.min_n = max(1, round(min_speech_ms / W))
        self.pre_n = max(1, round(preroll_ms / W)); self.on, self.off = on, off; self.max_n = int(max_ms / W); self.min_rms = min_rms
        self.early_n = round(early_ms / W) if 0 < early_ms < silence_ms else 0
        self.reset()
    def reset(self):
        self.speaking = False; self.confirmed = False; self.seg = []; self.pre = []
        self.voiced_run = self.silent_run = self.voiced_total = 0; self.t_last_voice = None
    def _pcm(self): return b"".join(self.seg[: len(self.seg) - max(0, self.silent_run - 3)])
    def force_end(self):
        """Push-to-talk: закрыть реплику сейчас. None, если речи почти не было."""
        ok = self.speaking and self.voiced_total >= max(2, self.min_n // 2)
        ev = ("end", self._pcm(), self.t_last_voice or time.perf_counter(), self.voiced_total * self.W) if ok else None
        self.reset(); return ev
    def feed(self, data: bytes):
        self.buf += data; evs = []
        step = Silero.CHUNK * 2
        while len(self.buf) >= step:
            raw, self.buf = self.buf[:step], self.buf[step:]
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            p = self.vad.prob(x); now = time.perf_counter()
            loud = float(np.sqrt(np.mean(x * x))) >= self.min_rms
            voiced = p >= self.on and loud
            if not self.speaking:
                self.pre.append(raw); self.pre = self.pre[-self.pre_n:]
                self.voiced_run = self.voiced_run + 1 if voiced else 0
                if self.voiced_run >= self.onset_n:
                    self.speaking = True; self.seg = list(self.pre); self.pre = []
                    self.silent_run = 0; self.voiced_total = self.voiced_run; self.t_last_voice = now
            else:
                self.seg.append(raw)
                if voiced: self.voiced_total += 1; self.t_last_voice = now; self.silent_run = 0
                elif p >= self.off and loud: self.silent_run = 0
                else: self.silent_run += 1
            if not self.speaking: continue
            if not self.confirmed and self.voiced_total >= self.min_n:
                self.confirmed = True; evs.append(("start", now))
            if self.silent_run >= self.silence_n or len(self.seg) >= self.max_n:
                evs.append(("end", self._pcm(), self.t_last_voice, self.voiced_total * self.W) if self.confirmed else ("drop", now))
                self.reset()
            elif self.confirmed and self.early_n and self.silent_run == self.early_n:
                evs.append(("pause", self._pcm(), self.t_last_voice))
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
