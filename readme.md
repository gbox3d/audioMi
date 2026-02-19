# audioMi

PC 오디오(Loopback/Mic)를 실시간 캡처해 TCP로 브로드캐스트하는 Tkinter 기반 서버와 테스트 클라이언트 예제입니다.

- 캡처 모드: `Loopback` / `Mic` / `Both`
- 전송 포맷: PCM16 mono, 16kHz
- 엔디언: Little Endian (`<ii`, `<i`, `<iiB`)
- 현재 UI 버전: `0.1.3` (코드 기준)

## 구성

- `main.py`: Tkinter 서버 UI, 장치 선택, 서버 시작/정지
- `audio_module.py`: 오디오 캡처 스레드, dBFS 계산, 16kHz PCM 변환
- `net_server.py`: asyncio TCP 브로드캐스트 서버, PING/ACK 처리
- `utils.py`: 장치 조회 및 오디오 유틸
- `audio_client_save.py`: 수신 오디오를 WAV로 저장하는 테스트 클라이언트
- `sample.env`: 실행 설정 예시

## 요구사항

- Python `>=3.12`
- Windows 환경 (Loopback 캡처 전제)
- `uv` 권장

## 빠른 시작

1. 의존성 설치

```bash
uv sync --frozen
```

2. 환경 파일 준비 (`audioMi/.env`)

```env
HOST=0.0.0.0
PORT=26070
CHECKCODE=20250918
```

3. 서버 실행

```bash
python main.py
```

4. 테스트 클라이언트 실행(별도 터미널)

```bash
python audio_client_save.py
```

## 동작 흐름

1. `main.py`에서 캡처 모드/장치 선택
2. `AudioCapture`가 입력 오디오를 chunk 단위로 캡처
3. `float32 -> PCM16(16kHz)` 변환 후 `send_queue`에 적재
4. `NetAudioServer`가 접속 클라이언트 전원에게 동일 패킷 브로드캐스트

## 프로토콜

Little Endian 사용.

1. Client -> Server (PING)

- 헤더(8바이트): `<ii` = `(checkcode:int, cmd:int=99)`

2. Server -> Client (PING ACK)

- ACK(9바이트): `<iiB` = `(checkcode:int, cmd:int=99, status:byte=0)`

3. Server -> Client (Audio)

- 헤더(8바이트): `<ii` = `(checkcode:int, cmd:int)`
- 사이즈(4바이트): `<i` = `(data_len:int)`
- 데이터: `data_len` 바이트 PCM16 mono 16kHz

cmd 값:

- `0x01`: loopback 오디오
- `0x02`: mic 오디오

## 실행/빌드

개발 실행:

```bash
python main.py
```

PyInstaller 예시:

```bash
pyinstaller --onefile --windowed --name audioMi main.py
# 또는
pyinstaller audioMi.spec
```

## 알려진 사항

- `audio_client_save.py`는 `cmd=0x01`/`cmd=0x02`를 각각
  `capture_from_server_loopback.wav`, `capture_from_server_mic.wav`로 분리 저장합니다.
- 서버 전송 큐(`send_q`)가 가득 차면 신규 오디오 chunk는 드롭될 수 있습니다.

## 설정값

- `HOST`: 서버 바인드 주소
- `PORT`: 서버 포트
- `CHECKCODE`: 프로토콜 식별용 정수

`sample.env`를 복사해 `.env`로 사용하면 됩니다.
