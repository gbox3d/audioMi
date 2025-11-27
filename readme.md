# audioMi

**“PC 스피커(Loopback)로 나가는 소리를 실시간으로 캡처해서 TCP 서버로 송출하고, 클라이언트가 그걸 받아서 파일로 저장할 수 있는 유틸리티 세트”**입니다.

**통신방식은 Little Endian 기반**  

## setup

```bash
uv init
uv add soundcard==0.4.5 numpy==2.2.6 scipy==1.16.2
```

## quick setup
```bash
uv sync --frozen
```


## build

```bash
pyinstaller --onefile --windowed --name="audioMi"  main.py
pyinstaller --windowed --name="audioMi"  main.py

pyinstaller audioMi.spec
```

## 서버 

audio_module.py : 스피커(Loopback) 오디오 캡처 모듈

net_server.py : 여러 클라이언트에게 오디오를 푸시하는 TCP 서버 모듈 

net_server

main.py : Tkinter 기반 서버 UI (캡처 + 서버 제어) 

main

.env : HOST / PORT / CHECKCODE 설정 파일 

.env

## 클라이언트

audio_client_save.py(예제): 서버에 붙어서 받은 오디오를 파일로 저장하는 테스트 클라이언트

2. 파일별 상세 설명
2-1. .env – 설정 관리 파일 

.env

HOST=localhost
PORT=26070
CHECKCODE=20250918


서버/클라이언트 공통으로 사용할 수 있는 설정 값:

HOST : 서버 IP / 바인드 주소 (예: 0.0.0.0 또는 localhost)

PORT : TCP 포트 번호

CHECKCODE : 프로토콜 식별용 정수(매직 번호)

dotenv로 로드해서 코드 안에서 하드코딩 없이 사용.

2-2. audio_module.py – 스피커(Loopback) 캡처 모듈

주요 역할

시스템의 Loopback(스피커 출력) 장치를 찾아서

지정된 샘플레이트(기본 48 kHz)로 오디오를 캡처하고

서버 전송용으로 모노 / 16 kHz PCM16 으로 변환해서 큐에 넣는 모듈

주요 구성 요소

list_loopback_mics()

soundcard 라이브러리로 모든 마이크를 조회하고,
loopback 속성 또는 이름을 기반으로 Loopback 장치 목록을 반환.

기본 스피커의 .loopback_microphone()을 최우선으로 사용하려고 시도.

dbfs_from_chunk(chunk)

float32 오디오 배열을 받아

모노로 평균

RMS 계산

20 * log10(rms) 로 dBFS 계산

UI 게이지용 레벨 계산에 사용.

float32_to_pcm16_resampled(chunk, in_sr, out_sr)

float32 [-1,1] → 모노

resample_poly 로 in_sr → out_sr (예: 48k → 16k)

int16 로 스케일링 후 bytes 로 변환.

class AudioCapture

초기화 시:

sample_rate, target_sr, chunk,
level_callback, error_callback 를 인자로 받음.

start(mic, send_queue):

별도 스레드에서 mic.recorder를 열고

rec.record()로 계속 읽으면서:

level_callback(db) 호출 (UI 레벨 표시용)

변환된 PCM16(16 kHz)를 send_queue로 put_nowait

stop():

stop 이벤트를 세팅하고 스레드 join.

핵심 포인트

UI 또는 서버와 완전히 분리된 “순수 오디오 캡처 레이어”

어디서든 AudioCapture + Queue만 있으면 같은 로직 재사용 가능.

2-3. net_server.py – 오디오 브로드캐스트 서버 

net_server

주요 역할

asyncio 기반 TCP 서버

외부에서 전달받은 send_queue(PCM 바이트)를

현재 접속한 모든 클라이언트에 브로드캐스트

클라이언트에서 오는 PING(99)에 ACK 응답

핵심 상수

REQUEST_AUDIO = 0x01 : 오디오 전송 커맨드

REQUEST_PING = 99 : 클라이언트 → 서버 핑 요청 코드

클래스 NetAudioServer

생성자 인자:

send_queue: 오디오 PCM 바이트가 들어오는 Queue

checkcode : 프로토콜 매직 넘버 (헤더 검증용)

host, port: 바인드 주소/포트

status_cb: 서버/클라 상태를 UI 쪽에 전달하는 콜백

내부 구성:

_clients: Set[StreamWriter]

현재 접속 중인 클라이언트 세션 목록

start() / stop()

별도 스레드에서 asyncio.run()으로 서버 루프 실행/종료

_async_main()

asyncio.start_server(self._handle_client, ...) 로 리스닝 시작

동시에 _broadcast_loop() 태스크를 띄움

stop 이벤트가 걸릴 때까지 유지

_handle_client(reader, writer)

클라이언트 접속 시:

_clients에 추가

상태 로그 출력 (접속 수 포함)

수신 루프:

readexactly(8)로 헤더(checkcode, cmd) 읽기

checkcode 불일치 시 종료

cmd == REQUEST_PING(99)이면:

!iiB (checkcode, 99, status=0) 로 ACK 송신

그 외 커맨드는 현재는 로그만 찍고 무시

종료 시 _clients에서 제거 + 로그 출력

_broadcast_loop()

send_queue에서 오디오 데이터(PCM 바이트) 꺼내기

클라이언트가 없으면 버리고 재시도

있으면:

header = struct.pack("!ii", checkcode, REQUEST_AUDIO)

size = struct.pack("!i", len(data))

packet = header + size + data

모든 클라에 write → drain

에러난 클라는 목록에서 제거 및 종료

핵심 포인트

서버는 “푸시(publish)”만 담당; 클라가 별도로 “요청”해서 받는 구조가 아님.

하나의 오디오 큐에서 가져온 데이터를 여러 클라이언트에게 동시에 전달.

2-4. main.py – Tkinter 기반 서버 UI 

main

역할:

Loopback 캡처 + 오디오 서버를 GUI로 제어

현재 오디오 레벨, 접속 중인 클라이언트 수, 상태 메시지를 표시

.env에서 HOST, PORT, CHECKCODE를 불러와 초기값으로 사용

주요 포인트

dotenv 설정 로드

from dotenv import load_dotenv
import os

load_dotenv()
self.default_host = os.getenv("HOST", "0.0.0.0")
self.default_port = os.getenv("PORT", "26070")
self.default_checkcode = os.getenv("CHECKCODE", "20250918")


UI 생성 시 Entry 기본값으로 삽입:

self.ent_host.insert(0, self.default_host)
self.ent_port.insert(0, self.default_port)
self.ent_checkcode.insert(0, self.default_checkcode)


UI 요소

Loopback 장치 선택 콤보박스

Host / Port / Checkcode 입력 필드

오디오 레벨 Progressbar + dBFS 라벨

클라이언트 수 라벨: Clients: N

상태바(StatusBar): 서버/오디오 상태 메세지

버튼:

[캡처 + 서버 시작]

[정지]

버튼 동작

_start():

선택된 Loopback 장치로 AudioCapture 시작

NetAudioServer 생성 후 start() 호출

버튼 상태 토글

_stop():

서버/오디오 모두 stop()

버튼 상태 원복

UI 업데이트 루프 _ui_tick()

self.ui_q에서 이벤트를 뽑아서 처리:

"status": 상태바 업데이트 + 콘솔 출력

"level": dBFS 스무딩 후 게이지/라벨 갱신

"server_event": 서버에서 올라온 이벤트 처리

ev_tag == "status" → 상태바 텍스트 갱신

ev_tag == "client_count" → Clients: N 라벨 갱신

"error": 에러 메시지 표시 후 자동 정지

핵심 포인트

서버 로직은 스레드 + asyncio,
UI 로직은 Tkinter mainloop 로 완전히 분리 → Queue로만 통신.

.env를 사용해서 HOST/PORT/CHECKCODE를 한 곳에서 관리. 

main

2-5. audio_client_save.py – 테스트 클라이언트 

net_client

역할:

서버에 접속하여

99번 PING 전송 → ACK 확인

이후 서버가 푸시하는 **오디오 패킷(cmd=1)**을 계속 받아서

로컬 WAV 파일(예: capture_from_server.wav)로 저장

주요 흐름:

.env에서 HOST / PORT / CHECKCODE 로드 (동일 값 사용)

asyncio.open_connection(host, port) 로 연결

PING:

ping_packet = struct.pack("!ii", checkcode, REQUEST_PING)
writer.write(ping_packet)
await writer.drain()

ack = await reader.readexactly(9)
recv_checkcode, cmd, status = struct.unpack("!iiB", ack)


오디오 패킷 수신 루프:

header = await reader.readexactly(8)
h_check, cmd = struct.unpack("!ii", header)
# cmd == REQUEST_AUDIO 인지 확인

size_raw = await reader.readexactly(4)
(size,) = struct.unpack("!i", size_raw)

data = await reader.readexactly(size)
wf.writeframesraw(data)    # WAV 파일에 누적


Ctrl + C 또는 서버 종료 시:

WAV 파일 닫기

총 바이트/초 길이 출력

3. 통신 프로토콜 요약

엔디언: ! → network(big-endian)

3-1. 클라이언트 → 서버 (PING)
[8바이트] !ii = (checkcode:int, cmd:int=99)


서버 응답:

[9바이트] !iiB = (checkcode:int, cmd:int=99, status:byte=0)

3-2. 서버 → 클라이언트 (오디오 스트림)
[8바이트] header = !ii = (checkcode:int, cmd:int=1)
[4바이트] size   = !i  = (data_len:int)
[size]    data   = PCM16 mono 16kHz raw bytes


클라이언트는 헤더/사이즈를 읽고, 그 길이만큼 readexactly로 data를 읽어 파일에 쓴다.