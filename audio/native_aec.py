"""Optional native audio backend for Jarvis AEC hot paths.

This module deliberately uses ``ctypes`` instead of a CPython extension module:
the native library is an implementation detail that can be missing, rebuilt, or
restarted independently from the Python audio pipeline. If loading fails, callers
can fall back to the existing NumPy implementation.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_BIN_DIR = ROOT / "native" / "bin"


class NativeAudioError(RuntimeError):
    """Raised when the native audio backend cannot be used."""


@dataclass(frozen=True)
class NativeAudioStatus:
    available: bool
    path: Path | None
    detail: str
    version: str | None = None


_LIB = None
_STATUS: NativeAudioStatus | None = None


def _default_library_path() -> Path:
    if sys.platform == "win32":
        name = "jarvis_audio_native.dll"
    elif sys.platform == "darwin":
        name = "libjarvis_audio_native.dylib"
    else:
        name = "libjarvis_audio_native.so"
    return DEFAULT_BIN_DIR / name


def _configured_library_path() -> Path:
    raw = os.environ.get("JARVIS_AUDIO_NATIVE_LIB", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _default_library_path()


def _load_library():
    global _LIB, _STATUS
    if _LIB is not None:
        return _LIB
    if _STATUS is not None and not _STATUS.available:
        raise NativeAudioError(_STATUS.detail)

    path = _configured_library_path()
    if not path.exists():
        _STATUS = NativeAudioStatus(
            available=False,
            path=path,
            detail=f"native audio library not found: {path}",
        )
        raise NativeAudioError(_STATUS.detail)

    try:
        lib = ctypes.CDLL(str(path))
        _configure_signatures(lib)
        version = lib.jarvis_audio_native_version().decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - depends on local toolchain/DLL
        _STATUS = NativeAudioStatus(
            available=False,
            path=path,
            detail=f"failed to load native audio library: {type(exc).__name__}: {exc}",
        )
        raise NativeAudioError(_STATUS.detail) from exc

    _LIB = lib
    _STATUS = NativeAudioStatus(
        available=True,
        path=path,
        detail="native audio library loaded",
        version=version,
    )
    return lib


def _configure_signatures(lib) -> None:  # noqa: ANN001
    f32_ptr = np.ctypeslib.ndpointer(dtype=np.float32, ndim=1, flags="C_CONTIGUOUS")

    lib.jarvis_audio_native_version.argtypes = []
    lib.jarvis_audio_native_version.restype = ctypes.c_char_p

    lib.jarvis_aec_create.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_float,
        ctypes.c_float,
        ctypes.c_float,
        ctypes.c_float,
    ]
    lib.jarvis_aec_create.restype = ctypes.c_void_p

    lib.jarvis_aec_destroy.argtypes = [ctypes.c_void_p]
    lib.jarvis_aec_destroy.restype = None

    lib.jarvis_aec_reset.argtypes = [ctypes.c_void_p]
    lib.jarvis_aec_reset.restype = None

    lib.jarvis_aec_process.argtypes = [
        ctypes.c_void_p,
        f32_ptr,
        f32_ptr,
        ctypes.c_int,
        f32_ptr,
    ]
    lib.jarvis_aec_process.restype = ctypes.c_int

    lib.jarvis_aec_last_erle_db.argtypes = [ctypes.c_void_p]
    lib.jarvis_aec_last_erle_db.restype = ctypes.c_float

    lib.jarvis_resample_24k_to_16k_f32.argtypes = [
        f32_ptr,
        ctypes.c_int,
        f32_ptr,
        ctypes.c_int,
    ]
    lib.jarvis_resample_24k_to_16k_f32.restype = ctypes.c_int


def native_audio_status() -> NativeAudioStatus:
    """Return availability without raising."""
    try:
        _load_library()
    except NativeAudioError:
        pass
    assert _STATUS is not None
    return _STATUS


def native_audio_available() -> bool:
    return native_audio_status().available


class NativeEchoCanceller:
    """ctypes wrapper matching the Python EchoCanceller process/reset contract."""

    backend_name = "native"

    def __init__(
        self,
        block: int = 512,
        partitions: int = 8,
        mu: float = 0.3,
        eps: float = 1e-6,
        dtd_factor: float = 0.5,
        converge_thresh_db: float = 6.0,
    ) -> None:
        self.N = int(block)
        self.P = int(partitions)
        self.taps = self._resolve_taps(self.N, self.P)
        self._lib = _load_library()
        self._handle = self._lib.jarvis_aec_create(
            self.N,
            self.taps,
            float(mu),
            float(eps),
            float(dtd_factor),
            float(converge_thresh_db),
        )
        if not self._handle:
            raise NativeAudioError("jarvis_aec_create returned null")

    @staticmethod
    def _resolve_taps(block: int, partitions: int) -> int:
        raw = os.environ.get("JARVIS_AUDIO_NATIVE_TAPS", "").strip()
        if raw:
            try:
                return max(block, int(raw))
            except ValueError as exc:
                raise NativeAudioError(
                    f"invalid JARVIS_AUDIO_NATIVE_TAPS={raw!r}"
                ) from exc
        # The native backend is time-domain NLMS: excellent for direct, low
        # latency echo, but not meant to replace the NumPy FFT backend for long
        # room tails. Cap default taps to keep CPU predictable in the audio path.
        return max(block, min(block * partitions, 1024))

    def __del__(self) -> None:  # pragma: no cover - destructor timing is runtime-specific
        handle = getattr(self, "_handle", None)
        lib = getattr(self, "_lib", None)
        if handle and lib:
            try:
                lib.jarvis_aec_destroy(handle)
            except Exception:
                pass
            self._handle = None

    def reset(self) -> None:
        self._lib.jarvis_aec_reset(self._handle)

    def process(self, near: np.ndarray, far: np.ndarray) -> np.ndarray:
        near_f = np.ascontiguousarray(near, dtype=np.float32)
        far_f = np.ascontiguousarray(far, dtype=np.float32)
        if near_f.shape != far_f.shape:
            raise ValueError("near and far must have the same shape")
        if near_f.ndim != 1:
            raise ValueError("near and far must be 1-D arrays")
        out = np.empty_like(near_f)
        ok = self._lib.jarvis_aec_process(
            self._handle,
            near_f,
            far_f,
            int(near_f.size),
            out,
        )
        if ok != 0:
            raise NativeAudioError(f"jarvis_aec_process failed with code {ok}")
        return out

    @property
    def last_erle_db(self) -> float:
        return float(self._lib.jarvis_aec_last_erle_db(self._handle))


def resample_24k_to_16k_native(pcm_24k: np.ndarray) -> np.ndarray:
    """Fast native 24 kHz -> 16 kHz linear resampler for far-end AEC reference."""
    lib = _load_library()
    src = np.ascontiguousarray(pcm_24k, dtype=np.float32)
    if src.size == 0:
        return src
    # Ceil(n * 2 / 3), matching scipy.signal.resample_poly output sizing.
    out_cap = int((src.size * 2 + 2) // 3)
    out = np.empty(out_cap, dtype=np.float32)
    n = lib.jarvis_resample_24k_to_16k_f32(src, int(src.size), out, int(out_cap))
    if n < 0:
        raise NativeAudioError(f"native resampler failed with code {n}")
    return out[:n]
