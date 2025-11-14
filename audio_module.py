# audio_module.py
import threading
import queue
from typing import Optional, Callable

import numpy as np
import soundcard as sc
from scipy.signal import resample_poly

DEFAULT_SAMPLE_RATE = 48000   # loopback 캡처
DEFAULT_TARGET_SR = 16000     # 네트워크 전송용
DEFAULT_CHUNK = 1024


def list_loopback_mics():
    """
    Loopback 장치 목록 반환.
    기본 스피커의 loopback 을 최우선으로 배치.
    """
    mics = sc.all_microphones(include_loopback=True)

    def is_loopback(m):
        return getattr(m, "isloopback", False) or "loopback" in m.name.lower()

    loopbacks = [m for m in mics if is_loopback(m)]
    try:
        default_lb = sc.default_speaker().loopback_microphone()
        loopbacks = [default_lb] + [m for m in loopbacks if m.name != default_lb.name]
    except Exception:
        pass

    return loopbacks if loopbacks else mics


def dbfs_from_chunk(chunk: np.ndarray) -> float:
    """float32 오디오 청크로부터 dBFS 계산."""
    if chunk.ndim == 2:
        mono = np.mean(chunk, axis=1)
    else:
        mono = chunk
    rms = np.sqrt(np.mean(np.square(mono), dtype=np.float64) + 1e-12)
    return min(20 * np.log10(rms + 1e-12), 0.0)


def float32_to_pcm16_resampled(chunk: np.ndarray, in_sr: int, out_sr: int) -> bytes:
    """
    float32 [-1,1] → mono int16 → bytes
    필요 시 resample_poly로 리샘플링.
    """
    if chunk.ndim == 2:
        mono = np.mean(chunk, axis=1)
    else:
        mono = chunk

    mono = np.clip(mono, -1.0, 1.0)

    if in_sr != out_sr:
        gcd = np.gcd(in_sr, out_sr)
        up, down = out_sr // gcd, in_sr // gcd
        mono = resample_poly(mono, up, down)

    pcm16 = (mono * 32767.0).astype(np.int16)
    return pcm16.tobytes()


class AudioCapture:
    """
    Loopback 캡처 전용 스레드.
    - 계속 캡처해서 send_queue 로 PCM 바이트 밀어넣는 역할.
    - 필요하면 level_callback 으로 dBFS 모니터링 가능.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        target_sr: int = DEFAULT_TARGET_SR,
        chunk: int = DEFAULT_CHUNK,
        level_callback: Optional[Callable[[float], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.target_sr = target_sr
        self.chunk = chunk
        self.level_callback = level_callback
        self.error_callback = error_callback

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, mic, send_queue: queue.Queue) -> None:
        """캡처 스레드 시작."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_worker, args=(mic, send_queue), daemon=True
        )
        self._thread.start()

    def _capture_worker(self, mic, send_queue: queue.Queue) -> None:
        try:
            with mic.recorder(samplerate=self.sample_rate) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=self.chunk)

                    # dBFS 모니터링 콜백
                    if self.level_callback is not None:
                        try:
                            db = dbfs_from_chunk(data)
                            self.level_callback(db)
                        except Exception:
                            pass

                    # 서버 전송용 큐로 PCM16 (target_sr) 넣기
                    try:
                        pcm = float32_to_pcm16_resampled(
                            data, self.sample_rate, self.target_sr
                        )
                        send_queue.put_nowait(pcm)
                    except queue.Full:
                        # 버퍼가 가득 찼으면 과감히 버려도 됨
                        pass
        except Exception as e:
            if self.error_callback is not None:
                self.error_callback(e)

    def stop(self) -> None:
        """캡처 스레드 종료."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
