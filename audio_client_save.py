# audio_client_save.py
"""
Loopback Audio Server 테스트용 클라이언트

- 서버에 접속해서 PING(99) 전송
- 서버가 push 해주는 cmd=1(REQUEST_AUDIO) 오디오 패킷을 계속 받아서
  로컬 WAV 파일로 저장하는 예제

환경:
  uv add numpy  (numpy는 꼭 필요하진 않지만, 후처리용으로 쓰고 싶으면)

사용법:
  python audio_client_save.py
"""

import asyncio
import struct
import wave
import signal
import sys
from typing import Optional

REQUEST_AUDIO = 0x01
REQUEST_PING = 99

# ---- 서버 접속 설정 ----
HOST = "127.0.0.1"
PORT = 26070
CHECKCODE = 20250918

# ---- 저장 파일 / 포맷 ----
OUTPUT_WAV = "capture_from_server.wav"
WAV_CHANNELS = 1       # mono
WAV_SAMPLERATE = 16000 # 서버쪽에서 16kHz PCM16 보내는 것으로 가정
WAV_SAMPWIDTH = 2      # 16bit = 2 bytes


class GracefulExit(Exception):
    pass


def _setup_signal():
    """Ctrl+C (SIGINT) 에서 깔끔하게 빠지도록 설정"""
    loop = asyncio.get_event_loop()

    def handler(sig, frame):
        # 그냥 예외 하나 던져서 전체 루프 종료
        raise GracefulExit()

    signal.signal(signal.SIGINT, handler)
    if sys.platform == "win32":
        # 윈도우에선 signal + asyncio 조합이 좀 특이해서,
        # 그냥 기본 핸들러로도 충분히 동작함.
        pass


async def audio_client_save(
    host: str,
    port: int,
    checkcode: int,
    out_wav_path: str,
) -> None:
    print(f"[CLIENT] connect to {host}:{port} (checkcode={checkcode}) ...")

    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None

    # WAV 파일 열기
    wf = wave.open(out_wav_path, "wb")
    wf.setnchannels(WAV_CHANNELS)
    wf.setsampwidth(WAV_SAMPWIDTH)
    wf.setframerate(WAV_SAMPLERATE)

    total_bytes = 0

    try:
        reader, writer = await asyncio.open_connection(host, port)
        print("[CLIENT] connected")

        # ---- 1) PING 보내기 ----
        ping_packet = struct.pack("!ii", checkcode, REQUEST_PING)
        writer.write(ping_packet)
        await writer.drain()
        print("[CLIENT] ping sent")

        # ACK 읽기 ( !iiB = checkcode, cmd(=99), status )
        ack = await reader.readexactly(9)
        recv_checkcode, cmd, status = struct.unpack("!iiB", ack)

        if recv_checkcode != checkcode or cmd != REQUEST_PING or status != 0:
            print(
                f"[CLIENT] ping ACK invalid: check={recv_checkcode}, cmd={cmd}, status={status}"
            )
        else:
            print("[CLIENT] ping OK")

        print(
            "[CLIENT] waiting for audio packets... "
            "(Ctrl+C to stop, file will be saved on exit)"
        )

        # ---- 2) 오디오 패킷 수신 루프 ----
        while True:
            # header: !ii = (checkcode, cmd)
            header = await reader.readexactly(8)
            h_check, cmd = struct.unpack("!ii", header)

            if h_check != checkcode:
                print(f"[CLIENT] invalid checkcode in header: {h_check}")
                # 계속 받을지, 끊을지 선택 – 여기선 끊자
                break

            if cmd != REQUEST_AUDIO:
                # 다른 커맨드는 일단 무시
                print(f"[CLIENT] unknown cmd={cmd}, ignore payload")
                # 만약 서버가 이런 패킷에 size+data 를 붙였다면
                # size만큼 스킵해야 하지만,
                # 현재 프로토콜에서는 cmd=1에만 data가 붙는다고 가정.
                continue

            # size: !i
            size_raw = await reader.readexactly(4)
            (size,) = struct.unpack("!i", size_raw)

            if size <= 0:
                print(f"[CLIENT] invalid audio size={size}, skip")
                continue

            data = await reader.readexactly(size)
            wf.writeframesraw(data)
            total_bytes += len(data)

            # 너무 자주 출력하면 시끄러우니까 대략적인 통계만
            if total_bytes % (16000 * 2 * 5) < size:
                # 대략 5초마다 한번
                seconds = total_bytes / (WAV_SAMPLERATE * WAV_SAMPWIDTH)
                print(f"[CLIENT] received ~{seconds:5.1f} sec audio")

    except GracefulExit:
        print("\n[CLIENT] Ctrl+C detected, stopping...")
    except asyncio.IncompleteReadError:
        print("[CLIENT] connection closed by server")
    except Exception as e:
        print(f"[CLIENT] error: {e}")
    finally:
        # WAV 파일 닫기
        try:
            wf.close()
        except Exception:
            pass

        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        seconds = total_bytes / (WAV_SAMPLERATE * WAV_SAMPWIDTH) if total_bytes else 0
        print(
            f"[CLIENT] done. saved '{out_wav_path}' "
            f"({total_bytes} bytes, ~{seconds:0.1f} sec)"
        )


def main():
    _setup_signal()
    try:
        asyncio.run(audio_client_save(HOST, PORT, CHECKCODE, OUTPUT_WAV))
    except GracefulExit:
        # 여기까지 올 일은 거의 없지만, 혹시 모를 cleanup
        print("[CLIENT] exited")


if __name__ == "__main__":
    main()
