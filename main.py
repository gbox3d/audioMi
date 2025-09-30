########################################################
#위 주석은 수정하시 마세요.
#file: lookback_rms.py
#author: gbox3d
#date: 2025-09-29
#환경 : uv add soundcard==0.4.5 numpy scipy
#desc: 스피커 출력(Loopback) 오디오 캡처 후 RMS dBFS 게이지 표시.
#########################################################

import threading
import queue
import numpy as np
import soundcard as sc
import tkinter as tk
from tkinter import ttk, messagebox
import struct
import asyncio
from scipy.signal import resample_poly

REQUEST_STT  = 0x01
REQUEST_PING = 99


class App(tk.Tk):
    SAMPLE_RATE = 48000   # 캡처는 48kHz
    TARGET_SR   = 16000   # 서버 전송은 16kHz
    CHUNK       = 1024
    RMS_SMOOTH  = 0.2
    DBFS_FLOOR  = -60.0

    def __init__(self):
        super().__init__()
        self.title("Loopback RMS + Server (16kHz downsample)")

        # 상태
        self.audio_q = queue.Queue(maxsize=100)   # UI용
        self.send_q  = queue.Queue(maxsize=200)   # 서버 전송용
        self.stop_event = threading.Event()
        self.cap_thread = None
        self.net_thread = None
        self.current_dbfs = self.DBFS_FLOOR

        # UI 변수
        self.cmb_devices = None
        self.btn_start = None
        self.btn_stop = None
        self.lbl_db = None
        self.pbar = None
        self.statusbar = None
        self.ent_server_ip = None
        self.ent_server_port = None
        self.ent_checkcode = None
        self.var_wait_ack = tk.BooleanVar(value=False)
        self.var_ping_on_start = tk.BooleanVar(value=True)

        self._build_ui()
        self.after(33, self._ui_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 유틸 ----------
    def _list_loopback_mics(self):
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

    @staticmethod
    def _dbfs_from_chunk(chunk: np.ndarray) -> float:
        if chunk.ndim == 2:
            mono = np.mean(chunk, axis=1)
        else:
            mono = chunk
        rms = np.sqrt(np.mean(np.square(mono), dtype=np.float64) + 1e-12)
        return min(20 * np.log10(rms + 1e-12), 0.0)

    def _dbfs_to_percent(self, dbfs: float) -> int:
        v = (dbfs - self.DBFS_FLOOR) / (0.0 - self.DBFS_FLOOR)
        return int(round(max(0.0, min(1.0, v)) * 100))

    @staticmethod
    def _float32_to_pcm16_resampled(chunk: np.ndarray, in_sr: int, out_sr: int) -> bytes:
        """float32 [-1,1] → mono int16 bytes (resampled)"""
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

    # ---------- 캡처 ----------
    def _capture_worker(self, mic):
        try:
            with mic.recorder(samplerate=self.SAMPLE_RATE) as rec:
                while not self.stop_event.is_set():
                    data = rec.record(numframes=self.CHUNK)
                    # UI
                    try:
                        self.audio_q.put_nowait(data)
                    except queue.Full:
                        pass
                    # 전송용
                    try:
                        pcm = self._float32_to_pcm16_resampled(data, self.SAMPLE_RATE, self.TARGET_SR)
                        self.send_q.put_nowait(pcm)
                    except queue.Full:
                        pass
        except Exception as e:
            # self.audio_q.put(('__error__', f"CaptureError: {e}"))
            # self.audio_q.put(('error', f"CaptureError: {e}"))
            print(f"CaptureError: {e}")

    # ---------- 네트워킹 ----------
    async def _net_sender(self, host, port, checkcode, wait_ack, do_ping):
        self._set_status(f"[NET] connect {host}:{port} ...")
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except Exception as e:
            self._set_status(f"[NET] connect fail: {e}")
            return

        self._set_status("[NET] connected")

        if do_ping:
            try:
                writer.write(struct.pack("!ii", checkcode, REQUEST_PING))
                await writer.drain()
                ack = await reader.readexactly(9)
                _, _, st = struct.unpack("!iiB", ack)
                self._set_status(f"[NET] ping status={st}")
            except Exception as e:
                self._set_status(f"[NET] ping fail: {e}")

        try:
            while not self.stop_event.is_set():
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
                        _, _, st = struct.unpack("!iiB", ack)
                        if st != 0:
                            self._set_status(f"[NET] status={st}")
                except Exception as e:
                    self._set_status(f"[NET] send fail: {e}")
                    break
        finally:
            writer.close()
            await writer.wait_closed()
            self._set_status("[NET] disconnected")

    def _net_thread(self, host, port, checkcode, wait_ack, do_ping):
        
        print(f"Net thread start: {host}:{port}, checkcode={checkcode}, wait_ack={wait_ack}, ping={do_ping}")
        self._set_status("전송 시작")

        asyncio.run(self._net_sender(host, port, checkcode, wait_ack, do_ping))

    # ---------- UI 루프 ----------
    def _ui_tick(self):
        got = False
        try:
            while True:
                item = self.audio_q.get_nowait()
                if isinstance(item, tuple) and "Error" in item[0]:
                    self._set_status(item[1])
                    self._stop_capture_ui()
                    messagebox.showerror("오디오 오류", item[1])
                    break
                chunk = item
                db = self._dbfs_from_chunk(chunk)

                item = self.audio_q.get_nowait()

                # 1) ndarray = 정상 오디오 프레임
                if isinstance(item, np.ndarray):
                    db = self._dbfs_from_chunk(item)
                    self.current_dbfs = (self.RMS_SMOOTH * self.current_dbfs) + ((1 - self.RMS_SMOOTH) * db)
                    got = True
                    continue

                # 2) 튜플/메시지 = 상태/에러
                if isinstance(item, tuple) and len(item) >= 2:
                    tag = str(item[0]).lower().lstrip('_')  # '__error__' -> 'error'
                    msg = str(item[1])
                    if 'error' in tag:
                        self._set_status(msg)
                        self._stop_capture_ui()
                        messagebox.showerror("오디오 오류", msg)
                        break
                    # 알 수 없는 메시지는 무시
                    continue

                # 3) 기타 타입도 무시
                continue

        except queue.Empty:
            pass

        if got and self.pbar and self.lbl_db:
            pct = self._dbfs_to_percent(self.current_dbfs)
            self.pbar['value'] = pct
            self.lbl_db.config(text=f"RMS: {self.current_dbfs:6.1f} dBFS ({pct:3d}%)")

        self.after(33, self._ui_tick)

    # ---------- 버튼 ----------
    def _start(self):
        if self.cap_thread and self.cap_thread.is_alive():
            return
        mics = self._list_loopback_mics()
        idx = self.cmb_devices.current()
        if idx < 0:
            messagebox.showwarning("장치", "Loopback 장치를 선택하세요")
            return
        mic = mics[idx]

        self.stop_event.clear()
        self.cap_thread = threading.Thread(target=self._capture_worker, args=(mic,), daemon=True)
        self.cap_thread.start()
        
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.btn_connect.config(state='normal')
        self.btn_disconnect.config(state='disabled')
        self._set_status("캡처시작")

    def _stop(self):
        self._stop_capture_ui()
        self._set_status("중지됨")

    def _stop_capture_ui(self):
        self.stop_event.set()
        self.btn_start.config(state='normal')
        self.btn_stop.config(state='disabled')
        self.btn_connect.config(state='disabled')
        self.btn_disconnect.config(state='disabled')


    def _on_connect(self):
        host = self.ent_server_ip.get().strip()
        port = int(self.ent_server_port.get())
        checkcode = int(self.ent_checkcode.get())
        self.net_thread = threading.Thread(
            target=self._net_thread,
            args=(host, port, checkcode,
                    self.var_wait_ack.get(),
                    self.var_ping_on_start.get()),
            daemon=True)
        self.net_thread.start()

        self.btn_connect.config(state='disabled')
        self.btn_disconnect.config(state='normal')

        self.btn_connect.config(state='normal')
        self.btn_disconnect.config(state='disabled')
        self._set_status("대기 중")


    def _on_disconnect(self):
        self.stop_event.set()
        if self.net_thread:
            self.net_thread.join()
            self.net_thread = None
        self.btn_connect.config(state='normal')
        self.btn_disconnect.config(state='disabled')
        self._set_status("연결끊김")


    def _on_close(self):
        self.stop_event.set()
        self.destroy()

    # ---------- UI ----------
    def _build_ui(self):
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # 장치 선택
        mics = self._list_loopback_mics()
        names = [m.name for m in mics] if mics else ["(없음)"]
        self.cmb_devices = ttk.Combobox(frm, values=names, state="readonly", width=50)
        self.cmb_devices.pack(anchor="w")
        if mics:
            self.cmb_devices.current(0)

        # 서버 설정
        f2 = ttk.Frame(frm)
        f2.pack(fill="x", pady=8)
        ttk.Label(f2, text="IP").pack(side="left")
        self.ent_server_ip = ttk.Entry(f2, width=15)
        self.ent_server_ip.pack(side="left", padx=4)
        self.ent_server_ip.insert(0, "127.0.0.1")
        ttk.Label(f2, text="Port").pack(side="left")
        self.ent_server_port = ttk.Entry(f2, width=6)
        self.ent_server_port.pack(side="left", padx=4)
        self.ent_server_port.insert(0, "26070")
        ttk.Label(f2, text="Checkcode").pack(side="left")
        self.ent_checkcode = ttk.Entry(f2, width=10)
        self.ent_checkcode.pack(side="left", padx=4)
        self.ent_checkcode.insert(0, "20250918")
        ttk.Checkbutton(f2, text="Ping(99)", variable=self.var_ping_on_start).pack(side="left", padx=4)
        ttk.Checkbutton(f2, text="ACK 대기", variable=self.var_wait_ack).pack(side="left", padx=4)

        # 게이지
        self.pbar = ttk.Progressbar(frm, orient="horizontal", length=420, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", pady=6)
        self.lbl_db = ttk.Label(frm, text="RMS: --- dBFS")
        self.lbl_db.pack(anchor="w")

        # 버튼
        f3 = ttk.Frame(frm)
        f3.pack(fill="x", pady=6)
        self.btn_start = ttk.Button(f3, text="시작", command=self._start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(f3, text="정지", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)

        self.btn_disconnect = ttk.Button(f3, text="연결끊기", command=self._on_disconnect, state="disabled")
        self.btn_disconnect.pack(side="right", padx=4)
        self.btn_connect = ttk.Button(f3, text="연결", command=self._on_connect , state="disabled")
        self.btn_connect.pack(side="right", padx=4)        

        # 상태
        self.statusbar = ttk.Label(self, text="대기 중", anchor="w", relief="sunken")
        self.statusbar.pack(side="bottom", fill="x")

    def _set_status(self, msg):
        if self.statusbar:
            self.statusbar.config(text=msg)


if __name__ == "__main__":
    App().mainloop()
