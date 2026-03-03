# audio_client_save.py
"""
Audio Server 테스트용 클라이언트

- 서버에 접속해서 PING(99) 전송
- 서버가 push 해주는 cmd=1(loopback), cmd=2(mic) 오디오 패킷을 받아서
  로컬 WAV 파일로 저장하는 예제

사용법:
  python audio_client_save.py
"""

import asyncio
import signal
import struct
import sys
from typing import Optional
import wave

REQUEST_AUDIO_LOOPBACK = 0x01
REQUEST_AUDIO_MIC = 0x02
REQUEST_PING = 99

# ---- 서버 접속 설정 ----
HOST = "127.0.0.1"
PORT = 26070
CHECKCODE = 20250918
RECONNECT_DELAY_SEC = 3.0
CONNECT_TIMEOUT_SEC = 5.0

# ---- 저장 파일 / 포맷 ----
OUTPUT_WAV_LOOPBACK = "capture_from_server_loopback.wav"
OUTPUT_WAV_MIC = "capture_from_server_mic.wav"
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
    out_wav_loopback_path: str,
    out_wav_mic_path: str,
) -> None:
    # WAV 파일 열기 (cmd별 분리 저장)
    wf_loopback = wave.open(out_wav_loopback_path, "wb")
    wf_loopback.setnchannels(WAV_CHANNELS)
    wf_loopback.setsampwidth(WAV_SAMPWIDTH)
    wf_loopback.setframerate(WAV_SAMPLERATE)

    wf_mic = wave.open(out_wav_mic_path, "wb")
    wf_mic.setnchannels(WAV_CHANNELS)
    wf_mic.setsampwidth(WAV_SAMPWIDTH)
    wf_mic.setframerate(WAV_SAMPLERATE)

    total_bytes_loopback = 0
    total_bytes_mic = 0
    attempt = 0

    try:
        while True:
            reader: Optional[asyncio.StreamReader] = None
            writer: Optional[asyncio.StreamWriter] = None
            attempt += 1

            try:
                print(
                    f"[CLIENT] connect attempt #{attempt} "
                    f"to {host}:{port} (checkcode={checkcode}) ..."
                )
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=CONNECT_TIMEOUT_SEC,
                )
                print("[CLIENT] connected")

                # ---- 1) PING 보내기 ----
                ping_packet = struct.pack("<ii", checkcode, REQUEST_PING)
                writer.write(ping_packet)
                await writer.drain()
                print("[CLIENT] ping sent")

                # ACK 읽기 ( <iiB = checkcode, cmd(=99), status )
                ack = await reader.readexactly(9)
                recv_checkcode, cmd, status = struct.unpack("<iiB", ack)

                if recv_checkcode != checkcode or cmd != REQUEST_PING or status != 0:
                    raise ConnectionError(
                        "[CLIENT] ping ACK invalid: "
                        f"check={recv_checkcode}, cmd={cmd}, status={status}"
                    )

                print("[CLIENT] ping OK")
                print(
                    "[CLIENT] waiting for audio packets... "
                    "(Ctrl+C to stop, files will be saved on exit)"
                )

                # ---- 2) 오디오 패킷 수신 루프 ----
                while True:
                    # header: <ii = (checkcode, cmd)
                    header = await reader.readexactly(8)
                    h_check, cmd = struct.unpack("<ii", header)

                    if h_check != checkcode:
                        raise ConnectionError(
                            f"[CLIENT] invalid checkcode in header: {h_check}"
                        )

                    # size: <i
                    size_raw = await reader.readexactly(4)
                    (size,) = struct.unpack("<i", size_raw)

                    if size <= 0:
                        print(f"[CLIENT] invalid audio size={size}, skip")
                        continue

                    data = await reader.readexactly(size)

                    if cmd == REQUEST_AUDIO_LOOPBACK:
                        wf_loopback.writeframesraw(data)
                        total_bytes_loopback += len(data)
                        if total_bytes_loopback % (16000 * 2 * 5) < size:
                            seconds = total_bytes_loopback / (
                                WAV_SAMPLERATE * WAV_SAMPWIDTH
                            )
                            print(f"[CLIENT] loopback received ~{seconds:5.1f} sec")
                    elif cmd == REQUEST_AUDIO_MIC:
                        wf_mic.writeframesraw(data)
                        total_bytes_mic += len(data)
                        if total_bytes_mic % (16000 * 2 * 5) < size:
                            seconds = total_bytes_mic / (
                                WAV_SAMPLERATE * WAV_SAMPWIDTH
                            )
                            print(f"[CLIENT] mic received ~{seconds:5.1f} sec")
                    else:
                        # 알 수 없는 커맨드도 size/data는 읽어서 스트림 동기 유지
                        print(
                            f"[CLIENT] unknown cmd={cmd}, "
                            f"payload skipped ({size} bytes)"
                        )

            except GracefulExit:
                raise
            except asyncio.IncompleteReadError:
                print("[CLIENT] connection closed by server")
            except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                print(f"{e}")
            except Exception as e:
                print(f"[CLIENT] error: {e}")
            finally:
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

            print(
                "[CLIENT] server unavailable, "
                f"retry in {RECONNECT_DELAY_SEC:0.1f} sec..."
            )
            await asyncio.sleep(RECONNECT_DELAY_SEC)

    except GracefulExit:
        print("\n[CLIENT] Ctrl+C detected, stopping...")
    finally:
        # WAV 파일 닫기
        try:
            wf_loopback.close()
        except Exception:
            pass
        try:
            wf_mic.close()
        except Exception:
            pass

        sec_loopback = (
            total_bytes_loopback / (WAV_SAMPLERATE * WAV_SAMPWIDTH)
            if total_bytes_loopback
            else 0
        )
        sec_mic = (
            total_bytes_mic / (WAV_SAMPLERATE * WAV_SAMPWIDTH)
            if total_bytes_mic
            else 0
        )
        print(
            f"[CLIENT] done. loopback='{out_wav_loopback_path}' "
            f"({total_bytes_loopback} bytes, ~{sec_loopback:0.1f} sec)"
        )
        print(
            f"[CLIENT] done. mic='{out_wav_mic_path}' "
            f"({total_bytes_mic} bytes, ~{sec_mic:0.1f} sec)"
        )


def main():
    _setup_signal()
    try:
        asyncio.run(
            audio_client_save(
                HOST,
                PORT,
                CHECKCODE,
                OUTPUT_WAV_LOOPBACK,
                OUTPUT_WAV_MIC,
            )
        )
    except GracefulExit:
        # 여기까지 올 일은 거의 없지만, 혹시 모를 cleanup
        print("[CLIENT] exited")


if __name__ == "__main__":
    main()
