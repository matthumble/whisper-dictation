"""
Whisper backend selection, model loading, transcription, warmup. Module-level
lock serializes model invocations so the warmup thread and the dictation thread
cannot collide on the GPU.
"""
import logging
import threading

import numpy as np
import whisper

try:
    import mlx_whisper
except ImportError:
    mlx_whisper = None

from dictation_config import (
    MLX_MODEL_REPO,
    MODEL_SIZE,
    SAMPLE_RATE,
    TRANSCRIPTION_BACKEND,
    WARMUP_AUDIO_SEC,
    WHISPER_MODEL_DIR,
)

log = logging.getLogger(__name__)

_lock = threading.Lock()
_backend = "openai"
_openai_model = None


def _load_openai_model():
    global _openai_model
    if _openai_model is None:
        log.info("Loading OpenAI Whisper '%s' model...", MODEL_SIZE)
        _openai_model = whisper.load_model(
            MODEL_SIZE, download_root=str(WHISPER_MODEL_DIR)
        )
        log.info("OpenAI Whisper model ready.")
    return _openai_model


if TRANSCRIPTION_BACKEND == "mlx" and mlx_whisper is not None:
    _backend = "mlx"
    log.info("Using MLX Whisper backend with '%s'.", MLX_MODEL_REPO)
elif TRANSCRIPTION_BACKEND == "mlx":
    log.warning(
        "MLX backend requested but mlx_whisper is unavailable; falling back to OpenAI Whisper."
    )
    _load_openai_model()
else:
    _load_openai_model()


def _mlx_transcribe(audio: np.ndarray) -> dict:
    if mlx_whisper is None:
        raise RuntimeError("mlx_whisper is not installed")

    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=MLX_MODEL_REPO,
        language="en",
        verbose=False,
    )


def transcribe_audio(audio: np.ndarray) -> dict:
    global _backend
    with _lock:
        if _backend == "mlx":
            try:
                return _mlx_transcribe(audio)
            except Exception:
                log.exception("MLX transcription failed; falling back to OpenAI Whisper.")
                _backend = "openai"

        model = _load_openai_model()
        return model.transcribe(audio, language="en", fp16=False)


def warm_up_model():
    try:
        silence = np.zeros(int(SAMPLE_RATE * WARMUP_AUDIO_SEC), dtype=np.float32)
        transcribe_audio(silence)
        log.info("Warmup transcription complete.")
    except Exception:
        log.exception("Warmup transcription failed.")
