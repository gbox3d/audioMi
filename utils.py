import numpy as np
import soundcard as sc
from scipy.signal import resample_poly


def list_loopback_mics():
    """
    Loopback 장치 목록 반환.
    기본 스피커의 loopback 을 최우선으로 배치.
    """
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


def dbfs_from_chunk(chunk: np.ndarray) -> float:
    """float32 오디오 청크로부터 dBFS 계산."""
    if chunk.ndim == 2:
        mono = np.mean(chunk, axis=1)
    else:
        mono = chunk
    rms = np.sqrt(np.mean(np.square(mono), dtype=np.float64) + 1e-12)
    return min(20 * np.log10(rms + 1e-12), 0.0)


def float32_to_pcm16_resampled(chunk: np.ndarray, in_sr: int, out_sr: int) -> bytes:
    """
    float32 [-1,1] → mono int16 → bytes
    필요 시 resample_poly로 리샘플링.
    """
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