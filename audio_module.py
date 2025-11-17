# audio_module.py
import threading
import queue
from typing import Optional, Callable

from utils import float32_to_pcm16_resampled, dbfs_from_chunk

DEFAULT_SAMPLE_RATE = 48000   # loopback 캡처
DEFAULT_TARGET_SR = 16000     # 네트워크 전송용
DEFAULT_CHUNK = 1024


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