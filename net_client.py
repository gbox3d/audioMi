import asyncio
import struct
import threading
import queue
from typing import Optional, Callable


REQUEST_STT = 0x01
REQUEST_PING = 99


StatusCallback = Callable[[str], None]
SignalCallback = Callable[[str, object], None]


class NetClient:
    """STT 서버로 PCM 전송을 담당하는 네트워크 모듈."""

    def __init__(
        self,
        send_queue: queue.Queue,
        status_cb: Optional[StatusCallback] = None,
        signal_cb: Optional[SignalCallback] = None,
    ) -> None:
        self.send_q = send_queue
        self.status_cb = status_cb or (lambda msg: None)
        self.signal_cb = signal_cb or (lambda name, payload=None: None)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # 내부 헬퍼 -------------------------------------------------
    def _post_status(self, msg: str) -> None:
        self.status_cb(msg)

    def _post_signal(self, name: str, payload=None) -> None:
        self.signal_cb(name, payload)

    # 외부 API -------------------------------------------------
    def connect(
        self,
        host: str,
        port: int,
        checkcode: int,
        wait_ack: bool = True,
        do_ping: bool = True,
    ) -> None:
        """백그라운드 스레드에서 서버 접속 및 전송 시작."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._net_thread,
            args=(host, port, checkcode, wait_ack, do_ping),
            daemon=True,
        )
        self._thread.start()

    def disconnect(self) -> None:
        """전송 스레드 종료."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    # 내부 구현 -------------------------------------------------
    def _net_thread(
        self, host: str, port: int, checkcode: int, wait_ack: bool, do_ping: bool
    ) -> None:
        self._post_status("전송 시작")
        self._post_status(f"[NET] connect {host}:{port} ...")
        try:
            asyncio.run(
                self._net_sender(host, port, checkcode, wait_ack, do_ping)
            )
        except Exception as e:
            self._post_status(f"[NET] fatal error: {e}")
            self._post_signal("net_send_fail", str(e))

    async def _net_sender(
        self,
        host: str,
        port: int,
        checkcode: int,
        wait_ack: bool,
        do_ping: bool,
    ) -> None:
        # 접속
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except Exception as e:
            self._post_status(f"[NET] connect fail: {e}")
            self._post_signal("net_connect_fail", str(e))
            return

        self._post_status("[NET] connected")
        self._post_signal("net_connected", None)

        # 옵션: PING
        if do_ping:
            try:
                writer.write(struct.pack("!ii", checkcode, REQUEST_PING))
                await writer.drain()
                ack = await reader.readexactly(9)
                _, _, st = struct.unpack("!iiB", ack)
                self._post_status(f"[NET] ping status={st}")
                self._post_signal("net_ping_ok", int(st))
            except Exception as e:
                self._post_status(f"[NET] ping fail: {e}")
                self._post_signal("net_send_fail", str(e))
        else:
            self._post_status("[NET] (ping skipped)")

        # 본 전송 루프
        try:
            while not self._stop_event.is_set():
                try:
                    data = self.send_q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                header = struct.pack("!ii", checkcode, REQUEST_STT)
                size = struct.pack("!i", len(data))
                try:
                    writer.write(header + size + data)
                    await writer.drain()

                    if wait_ack:
                        ack = await reader.readexactly(9)
                        _checkcode, _, st = struct.unpack("!iiB", ack)
                        if _checkcode != checkcode:
                            self._post_status(
                                f"[NET] ACK CHECKCODE mismatch: recv={_checkcode}, expected={checkcode}"
                            )
                            self._post_signal("net_send_fail", "ACK CHECKCODE mismatch")
                            break
                        if st != 0:
                            self._post_status(f"[NET] status={st}")
                            self._post_signal("net_ack_nonzero", int(st))
                except Exception as e:
                    self._post_status(f"[NET] send fail: {e}")
                    self._post_signal("net_send_fail", str(e))
                    break
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self._post_status("[NET] disconnected")
            self._post_signal("net_disconnected", None)
