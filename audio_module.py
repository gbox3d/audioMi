# audio_module.py
import threading
import queue
from typing import Optional, Callable

from utils import float32_to_pcm16_resampled, dbfs_from_chunk

DEFAULT_SAMPLE_RATE = 48000   # loopback 캡처
DEFAULT_TARGET_SR = 16000     # 네트워크 전송용
DEFAULT_CHUNK = 1024
DEFAULT_CMD_LOOPBACK = 0x01
DEFAULT_CMD_MIC = 0x02


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

    def _emit_level(self, source: str, db: float) -> None:
        if self.level_callback is None:
            return
        try:
            self.level_callback(source, db)
        except TypeError:
            self.level_callback(db)

    def _emit_error(self, source: str, err: Exception) -> None:
        if self.error_callback is None:
            return
        try:
            self.error_callback(source, err)
        except TypeError:
            self.error_callback(err)

    def start(
        self,
        mic,
        send_queue: queue.Queue,
        output_cmd: int = DEFAULT_CMD_LOOPBACK,
        source_name: str = "loopback",
    ) -> None:
        """캡처 스레드 시작."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_worker,
            args=(mic, send_queue, output_cmd, source_name),
            daemon=True,
        )
        self._thread.start()

    def _capture_worker(
        self,
        mic,
        send_queue: queue.Queue,
        output_cmd: int,
        source_name: str,
    ) -> None:
        try:
            with mic.recorder(samplerate=self.sample_rate) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=self.chunk)

                    # dBFS 모니터링 콜백
                    try:
                        db = dbfs_from_chunk(data)
                        self._emit_level(source_name, db)
                    except Exception:
                        pass

                    # 서버 전송용 큐로 PCM16 (target_sr) 넣기
                    try:
                        pcm = float32_to_pcm16_resampled(
                            data, self.sample_rate, self.target_sr
                        )
                        send_queue.put_nowait((output_cmd, pcm))
                    except queue.Full:
                        # 버퍼가 가득 찼으면 과감히 버려도 됨
                        pass
        except Exception as e:
            self._emit_error(source_name, e)

    def stop(self) -> None:
        """캡처 스레드 종료."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
