# net_server.py
import asyncio
import struct
import threading
import queue
from contextlib import suppress
from typing import Set, Optional, Callable

REQUEST_AUDIO = 0x01   # 1번 커맨드: 오디오 푸시
REQUEST_PING  = 99

# StatusCallback = Callable[[str], None]
StatusCallback = Callable[[str, object], None] 


class NetAudioServer:
    """
    오디오 스트림 서버.
    - 외부에서 send_queue 로 들어오는 PCM 청크를
      접속한 모든 클라이언트에 1번 커맨드로 브로드캐스트.
    - 클라이언트가 99(PING)을 보내면 ACK 응답.
    """

    def __init__(
        self,
        send_queue: queue.Queue,
        checkcode: int,
        host: str = "0.0.0.0",
        port: int = 26070,
        status_cb: Optional[StatusCallback] = None,
    ) -> None:
        self.send_queue = send_queue
        self.checkcode = checkcode
        self.host = host
        self.port = port
        self.status_cb = status_cb or (lambda msg: None)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: Set[asyncio.StreamWriter] = set()

    # ---------- 상태 출력 ----------    
    def _log(self, tag: str, payload=None):
        self.status_cb(tag, payload)

    # ---------- 외부 API ----------
    def start(self) -> None:
        """서버 스레드 시작."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._server_thread, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """서버 스레드 종료 요청."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---------- 내부 구현 ----------
    def _server_thread(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self._log(f"[SERVER] fatal: {e}")

    async def _async_main(self) -> None:
        self._log(f"[SERVER] listen on {self.host}:{self.port}")
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

        # 오디오 브로드캐스트 태스크
        broadcaster = asyncio.create_task(self._broadcast_loop())

        try:
            async with self._server:
                while not self._stop_event.is_set():
                    await asyncio.sleep(0.1)
        finally:
            broadcaster.cancel()
            with suppress(asyncio.CancelledError):
                await broadcaster

            # 클라이언트 모두 정리
            for w in list(self._clients):
                try:
                    w.close()
                    with suppress(Exception):
                        await w.wait_closed()
                except Exception:
                    pass
            self._clients.clear()

            self._log("[SERVER] stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        self._clients.add(writer)
        # self._log(f"[CLIENT] connected: {addr}, total={len(self._clients)}")
        self._log("status", f"[CLIENT] connected: {addr}, total={len(self._clients)}")
        self._log("client_count", len(self._clients))

        try:
            while not self._stop_event.is_set():
                try:
                    header = await reader.readexactly(8)
                except asyncio.IncompleteReadError:
                    break

                recv_checkcode, cmd = struct.unpack("!ii", header)
                if recv_checkcode != self.checkcode:
                    self._log(
                        f"[CLIENT {addr}] invalid checkcode: {recv_checkcode}"
                    )
                    break

                if cmd == REQUEST_PING:
                    # PING ACK
                    ack = struct.pack("!iiB", self.checkcode, REQUEST_PING, 0)
                    try:
                        writer.write(ack)
                        await writer.drain()
                    except Exception as e:
                        self._log(f"[CLIENT {addr}] ping ack fail: {e}")
                        break
                    self._log(f"[CLIENT {addr}] ping ok")

                else:
                    # 현재 프로토콜상 클라→서버로 다른 명령은 무시
                    self._log(f"[CLIENT {addr}] unknown cmd={cmd}, ignored")

        finally:
            self._clients.discard(writer)            
            try:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
            except Exception:
                pass
            self._log("status", f"[CLIENT] disconnected: {addr}, total={len(self._clients)}")
            self._log("client_count", len(self._clients))

    async def _broadcast_loop(self) -> None:
        """
        send_queue 에 들어온 오디오 청크를
        현재 접속한 모든 클라이언트로 전송.
        """
        while not self._stop_event.is_set():
            try:
                data = self.send_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue

            if not self._clients:
                # 접속자가 없으면 그냥 버림
                continue

            header = struct.pack("!ii", self.checkcode, REQUEST_AUDIO)
            size = struct.pack("!i", len(data))
            packet = header + size + data

            dead_clients = []

            # 전송
            for w in list(self._clients):
                try:
                    w.write(packet)
                except Exception:
                    dead_clients.append(w)
                    continue

            # drain
            for w in list(self._clients):
                if w in dead_clients:
                    continue
                try:
                    await w.drain()
                except Exception:
                    dead_clients.append(w)

            # 에러난 클라 정리
            for w in dead_clients:
                self._clients.discard(w)
                try:
                    w.close()
                    with suppress(Exception):
                        await w.wait_closed()
                except Exception:
                    pass
            if dead_clients:
                self._log(
                    f"[SERVER] removed {len(dead_clients)} dead clients, total={len(self._clients)}"
                )
