# main.py
#########################################################
# 위 주석은 수정하지 마세요.
# file: main.py (LE)
# author: gbox3d
# date: 2025-11-13
# env : uv add soundcard==0.4.5 numpy scipy
# desc: 스피커 출력(Loopback)을 캡처하여
#       TCP 클라이언트에 실시간 오디오 스트림(커맨드 1) 푸시.
#       Tkinter UI 로 서버를 제어.
#########################################################

from dotenv import load_dotenv
import os

import queue
import tkinter as tk
from tkinter import ttk, messagebox

from audio_module import AudioCapture
from utils import list_loopback_mics, dbfs_from_chunk  # ✅ 올바른 위치
from net_server import NetAudioServer

from etc import resource_path, get_base_dir

class App(tk.Tk):
    DBFS_FLOOR = -60.0
    RMS_SMOOTH = 0.2
    __VERSION__ = "0.1.1"

    def __init__(self):
        super().__init__()
        
        # 1. .env 파일 경로 설정 (실행파일과 같은 위치)
        env_path = os.path.join(get_base_dir(), ".env")
        
        # 2. 로드 시도 및 결과 확인
        is_loaded = load_dotenv(env_path)
        
        # 3. 환경 변수 읽기
        self.default_host = os.getenv("HOST", "0.0.0.0")
        self.default_port = os.getenv("PORT", "26070")
        self.default_checkcode = os.getenv("CHECKCODE", "20250918")
        
        
        self.title(f"Loopback Audio Server v{self.__VERSION__} LE")

        # 큐: UI 갱신용
        self.ui_q = queue.Queue(maxsize=200)

        # 오디오 데이터 전송용 큐
        self.send_q = queue.Queue(maxsize=200)

        self.mics = []
        self.audio_capture = None
        self.server = None

        self.current_dbfs = self.DBFS_FLOOR

        # UI 위젯 핸들
        self.cmb_devices = None
        self.ent_host = None
        self.ent_port = None
        self.ent_checkcode = None
        self.btn_start = None
        self.btn_stop = None
        self.pbar = None
        self.lbl_db = None
        self.statusbar = None

        self._build_ui()
        self._load_devices()

        self.after(33, self._ui_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # 4. 다이얼로그 표시 로직
        if is_loaded:
            msg = (f".env 설정을 불러왔습니다.\n\n"
                   f"Path: {env_path}\n"
                   f"HOST: {self.default_host}\n"
                   f"PORT: {self.default_port}\n"
                   f"CHECKCODE: {self.default_checkcode}")
            messagebox.showinfo("설정 로드 성공", msg)
        else:
            msg = (f".env 파일을 찾을 수 없어 기본값으로 시작합니다.\n\n"
                   f"시도한 경로: {env_path}\n"
                   f"기본 PORT: {self.default_port}")
            messagebox.showwarning("설정 로드 실패", msg)

    # ---------- 내부 유틸 ----------
    def _post_ui(self, item):
        try:
            self.ui_q.put_nowait(item)
        except queue.Full:
            pass

    def _log(self, tag: str, payload=None):
        self._post_ui(("server_event", tag, payload))

    def _on_audio_level(self, db: float):
        self._post_ui(("level", db))

    def _on_audio_error(self, e: Exception):
        self._post_ui(("error", f"[AUDIO] {e}"))

    def _dbfs_to_percent(self, dbfs: float) -> int:
        v = (dbfs - self.DBFS_FLOOR) / (0.0 - self.DBFS_FLOOR)
        return int(round(max(0.0, min(1.0, v)) * 100))

    # ---------- UI 빌드 ----------
    def _build_ui(self):
        
        icon_path = resource_path("icon.png")
        self.iconphoto(False, tk.PhotoImage(file=icon_path))
        
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # 장치 선택
        ttk.Label(frm, text="Loopback 장치").pack(anchor="w")
        self.cmb_devices = ttk.Combobox(frm, state="readonly", width=50)
        self.cmb_devices.pack(anchor="w", fill="x", pady=(0, 8))

        # 서버 설정
        f_srv = ttk.Frame(frm)
        f_srv.pack(fill="x", pady=4)

        ttk.Label(f_srv, text="Host").grid(row=0, column=0, sticky="w")
        self.ent_host = ttk.Entry(f_srv, width=15)
        self.ent_host.grid(row=0, column=1, padx=4)
        self.ent_host.insert(0, self.default_host)

        ttk.Label(f_srv, text="Port").grid(row=0, column=2, sticky="w")
        self.ent_port = ttk.Entry(f_srv, width=8)
        self.ent_port.grid(row=0, column=3, padx=4)
        self.ent_port.insert(0, self.default_port)

        ttk.Label(f_srv, text="Checkcode").grid(row=0, column=4, sticky="w")
        self.ent_checkcode = ttk.Entry(f_srv, width=12)
        self.ent_checkcode.grid(row=0, column=5, padx=4)
        self.ent_checkcode.insert(0, self.default_checkcode)

        for i in range(6):
            f_srv.columnconfigure(i, weight=1 if i in (1, 3, 5) else 0)

        # 레벨 바
        self.pbar = ttk.Progressbar(
            frm,
            orient="horizontal",
            length=420,
            mode="determinate",
            maximum=100,
        )
        self.pbar.pack(fill="x", pady=6)
        self.lbl_db = ttk.Label(frm, text="RMS: --- dBFS")
        self.lbl_db.pack(anchor="w")
        
        self.lbl_clients = ttk.Label(self, text="Clients: 0", anchor="w")
        self.lbl_clients.pack(side="bottom", fill="x")


        # 버튼
        f_btn = ttk.Frame(frm)
        f_btn.pack(fill="x", pady=8)

        self.btn_start = ttk.Button(f_btn, text="캡처 + 서버 시작", command=self._start)
        self.btn_start.pack(side="left", padx=4)

        self.btn_stop = ttk.Button(
            f_btn, text="정지", command=self._stop, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=4)

        # 상태바
        self.statusbar = ttk.Label(
            self, text="대기 중", anchor="w", relief="sunken"
        )
        self.statusbar.pack(side="bottom", fill="x")

    def _load_devices(self):
        self.mics = list_loopback_mics()
        if not self.mics:
            self.cmb_devices["values"] = ["(Loopback 장치 없음)"]
            self.cmb_devices.current(0)
            self._log("Loopback 장치를 찾을 수 없습니다.")
        else:
            names = [m.name for m in self.mics]
            self.cmb_devices["values"] = names
            self.cmb_devices.current(0)
            self._log(f"Loopback 장치 {len(self.mics)}개 발견")

    # ---------- UI 이벤트 ----------
    def _start(self):
        if not self.mics:
            messagebox.showerror("장치", "Loopback 장치를 찾을 수 없습니다.")
            return

        idx = self.cmb_devices.current()
        if idx < 0 or idx >= len(self.mics):
            messagebox.showerror("장치", "Loopback 장치를 선택하세요.")
            return
        mic = self.mics[idx]

        host = self.ent_host.get().strip()
        try:
            port = int(self.ent_port.get().strip())
            checkcode = int(self.ent_checkcode.get().strip())
        except ValueError:
            messagebox.showerror("설정", "Port/Checkcode 는 정수여야 합니다.")
            return

        # 오디오 캡처 시작
        self.audio_capture = AudioCapture(
            level_callback=self._on_audio_level,
            error_callback=self._on_audio_error,
        )
        self.audio_capture.start(mic, self.send_q)
        self._log(f"[AUDIO] capture started on '{mic.name}'")

        # 서버 시작
        self.server = NetAudioServer(
            send_queue=self.send_q,
            checkcode=checkcode,
            host=host,
            port=port,
            status_cb=self._log,
        )
        self.server.start()
        self._log(f"[SERVER] start listen on {host}:{port}, checkcode={checkcode}")

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def _stop(self):
        if self.server:
            self.server.stop()
            self.server = None
            self._log("[SERVER] stopped")

        if self.audio_capture:
            self.audio_capture.stop()
            self.audio_capture = None
            self._log("[AUDIO] capture stopped")

        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()

    # ---------- UI 틱 ----------
    def _ui_tick(self):
        got_level = False

        while True:
            try:
                item = self.ui_q.get_nowait()
            except queue.Empty:
                break

            if not item:
                continue

            tag = item[0]

            if tag == "status":
                _, msg = item
                if self.statusbar:
                    self.statusbar.config(text=msg)
                print(msg)

            elif tag == "level":
                _, db = item
                self.current_dbfs = (
                    self.RMS_SMOOTH * self.current_dbfs
                    + (1.0 - self.RMS_SMOOTH) * db
                )
                got_level = True
            elif tag == "server_event":
                _, ev_tag, payload = item
                if ev_tag == "status":
                    self.statusbar.config(text=payload)
                    print(payload)

                elif ev_tag == "client_count":
                    # UI 레이블 갱신
                    self.lbl_clients.config(text=f"Clients: {payload}")

            elif tag == "error":
                _, msg = item
                if self.statusbar:
                    self.statusbar.config(text=msg)
                print(msg)
                messagebox.showerror("오디오 오류", msg)
                # 오류 발생 시 자동 정지
                self._stop()
                break

        if got_level and self.pbar and self.lbl_db:
            pct = self._dbfs_to_percent(self.current_dbfs)
            self.pbar["value"] = pct
            self.lbl_db.config(
                text=f"RMS: {self.current_dbfs:6.1f} dBFS ({pct:3d}%)"
            )

        self.after(33, self._ui_tick)


if __name__ == "__main__":
    App().mainloop()