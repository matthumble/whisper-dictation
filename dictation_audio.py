"""
Audio device enumeration, stream open, RT callback factory, and resampling.
Note: the InputStream callback runs on PortAudio's high-priority thread. Any
function passed in as the callback MUST stay lock-light — see dictation_state.
"""
import logging
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from dictation_config import SAMPLE_RATE

log = logging.getLogger(__name__)


def resample_audio(
    audio: np.ndarray, source_sample_rate: float, target_sample_rate: int
) -> np.ndarray:
    if not audio.size or int(source_sample_rate) == int(target_sample_rate):
        return audio.astype(np.float32, copy=False)

    duration = len(audio) / source_sample_rate
    target_length = max(1, int(round(duration * target_sample_rate)))
    source_positions = np.linspace(0, len(audio) - 1, num=len(audio), dtype=np.float64)
    target_positions = np.linspace(0, len(audio) - 1, num=target_length, dtype=np.float64)
    resampled = np.interp(target_positions, source_positions, audio)
    return resampled.astype(np.float32, copy=False)


def _candidate_input_stream_configs() -> list[tuple[Optional[int], float, str]]:
    candidates: list[tuple[Optional[int], float, str]] = []
    seen: set[tuple[Optional[int], int]] = set()
    default_input_device = sd.default.device[0]
    devices = sd.query_devices()

    def add_candidate(device_id: Optional[int], sample_rate: float, label: str):
        key = (device_id, int(round(sample_rate)))
        if sample_rate <= 0 or key in seen:
            return
        seen.add(key)
        candidates.append((device_id, float(sample_rate), label))

    if default_input_device is not None and default_input_device >= 0:
        device_info = devices[default_input_device]
        add_candidate(
            default_input_device,
            float(device_info["default_samplerate"]),
            f"default input {device_info['name']}",
        )
        add_candidate(default_input_device, SAMPLE_RATE, f"default input {device_info['name']}")

    for fallback_rate in (48000, 44100):
        add_candidate(default_input_device, fallback_rate, "default input fallback")

    for device_id, device_info in enumerate(devices):
        if not device_info["max_input_channels"]:
            continue
        add_candidate(
            device_id,
            float(device_info["default_samplerate"]),
            f"fallback input {device_info['name']}",
        )

    return candidates


def open_input_stream(callback: Callable) -> tuple[sd.InputStream, float]:
    """Try each candidate input config until one opens. Returns (stream, sample_rate)."""
    failures: list[str] = []
    for device_id, sample_rate, label in _candidate_input_stream_configs():
        try:
            stream = sd.InputStream(
                device=device_id,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            log.info("Opened audio input using %s at %.0f Hz.", label, sample_rate)
            return stream, sample_rate
        except Exception as exc:
            failures.append(f"{label} @ {sample_rate:.0f} Hz: {exc}")

    raise RuntimeError(" ; ".join(failures) if failures else "No input devices available")
