"""
audio/aec.py - Cancelacion de eco acustico (AEC) para barge-in en parlantes.

Problema: en parlantes, la voz de Jarvis sale por el altavoz y vuelve al
microfono (eco). Ese eco tiene la misma energia y espectro que una voz humana,
asi que ningun VAD/umbral/wake-word lo distingue de Isaac (lo confirmamos con
datos en 3 intentos). La unica solucion robusta es CANCELAR el eco antes de
detectar: como conocemos exactamente lo que Jarvis reproduce (la "referencia" o
far-end), podemos estimar el eco que produce en el mic y restarlo.

Algoritmo: PBFDAF (Partitioned Block Frequency-Domain Adaptive Filter), el
estandar de AEC. Es un filtro adaptativo que aprende la respuesta-impulso del
camino de eco (parlante -> sala -> mic, incluido el retardo) y la resta. Trabaja
en el dominio de la frecuencia (overlap-save) por eficiencia, particionado para
cubrir colas de eco largas sin una FFT gigante.

  near (mic)  = voz_de_isaac + eco(far) + ruido
  far  (ref)  = lo que Jarvis reproduce
  cleaned     = near - eco_estimado  ≈ voz_de_isaac (+ residual)

Sobre `cleaned` el wake-word (o un VAD) ya puede detectar a Isaac.

Sin dependencias nativas: solo numpy (+ scipy para resampleo en el caller).
Todo a 16kHz mono float32 internamente.
"""

from __future__ import annotations

import threading

import numpy as np


class EchoCanceller:
    """Filtro adaptativo PBFDAF (overlap-save 50%, gradiente con restriccion).

    Procesa bloques de N muestras. El filtro total cubre N*partitions taps, o
    sea (N*partitions)/fs segundos de respuesta de eco (retardo + reverb).
    """

    def __init__(
        self,
        block: int = 512,
        partitions: int = 8,
        mu: float = 0.3,
        eps: float = 1e-6,
        dtd_factor: float = 0.5,
        converge_thresh_db: float = 6.0,
    ) -> None:
        self.N = block
        self.P = partitions
        self.nfft = 2 * block
        self.bins = block + 1  # rfft de 2N -> N+1 bins
        self.mu = mu
        self.eps = eps
        # Double-talk detection: una vez convergido (ERLE > converge_thresh), si
        # el residual supera dtd_factor * la potencia del eco estimado, asumimos
        # que entro la voz cercana (Isaac) y CONGELAMOS la adaptacion ese bloque
        # para no desajustar el filtro ya aprendido.
        self.dtd_factor = dtd_factor
        self.converge_thresh_db = converge_thresh_db
        self._converged = False
        # Pesos en frecuencia, una particion por bloque de retardo.
        self._W = np.zeros((self.P, self.bins), dtype=np.complex128)
        # Historia de far-end en frecuencia (Xh[0] = mas reciente).
        self._Xh = np.zeros((self.P, self.bins), dtype=np.complex128)
        # Ultimas N muestras de far-end (para la ventana overlap-save de 2N).
        self._x_prev = np.zeros(self.N, dtype=np.float64)
        self.last_erle_db = 0.0

    def reset(self) -> None:
        self._W[:] = 0
        self._Xh[:] = 0
        self._x_prev[:] = 0
        self.last_erle_db = 0.0
        self._converged = False

    def process(self, near: np.ndarray, far: np.ndarray) -> np.ndarray:
        """Cancela el eco de un bloque de N muestras. Devuelve `near` limpio.

        near, far: float32/float64 de longitud exacta N (rango ~[-1, 1]).
        """
        N = self.N
        near = near.astype(np.float64)
        far = far.astype(np.float64)

        # Ventana far-end de 2N (overlap-save): [bloque previo | bloque actual]
        xw = np.concatenate([self._x_prev, far])
        self._x_prev = far
        X = np.fft.rfft(xw)

        # Insertar X al frente de la historia de particiones (shift FIFO).
        self._Xh = np.roll(self._Xh, 1, axis=0)
        self._Xh[0] = X

        # Eco estimado = suma_p W_p * Xh_p ; tomar la mitad valida (overlap-save).
        Y = np.sum(self._W * self._Xh, axis=0)
        y = np.fft.irfft(Y, n=self.nfft)[N:]
        e = near - y  # near limpio (error del filtro)

        # Double-talk detection: si ya convergimos y el residual supera una
        # fraccion del eco estimado, hay voz cercana -> NO adaptar (preservar el
        # filtro). Antes de converger, adaptamos siempre (warm-up de eco-puro).
        echo_pow = float(np.mean(y ** 2))
        err_pow = float(np.mean(e ** 2))
        double_talk = self._converged and err_pow > self.dtd_factor * echo_pow

        if not double_talk:
            # Adaptacion NLMS en frecuencia, normalizada por potencia far-end.
            E = np.fft.rfft(np.concatenate([np.zeros(N), e]))
            power = np.sum(np.abs(self._Xh) ** 2, axis=0) + self.eps
            G = (np.conj(self._Xh) * E) / power  # (P, bins)
            # Restriccion de gradiente: pesos causales de N taps (anula 2da
            # mitad temporal). Estabiliza el PBFDAF.
            g_time = np.fft.irfft(G, n=self.nfft, axis=1)
            g_time[:, N:] = 0.0
            G = np.fft.rfft(g_time, axis=1)
            self._W += self.mu * G

        # ERLE (Echo Return Loss Enhancement): cuanto eco se quito, en dB.
        near_pow = float(np.mean(near ** 2))
        if near_pow > 1e-9:
            self.last_erle_db = 10.0 * np.log10(near_pow / (err_pow + 1e-12))
            if self.last_erle_db > self.converge_thresh_db:
                self._converged = True
        return e.astype(np.float32)


def resample_24k_to_16k(pcm_24k: np.ndarray) -> np.ndarray:
    """Resamplea float 24kHz -> 16kHz (ratio 2/3) via scipy.signal."""
    from scipy.signal import resample_poly

    return resample_poly(pcm_24k, up=2, down=3).astype(np.float32)


class AECStream:
    """Envoltorio streaming + thread-safe del EchoCanceller.

    El far-end (lo que reproduce el player) y el near-end (mic) llegan en
    threads distintos y a tasas/tamanos distintos. Esta clase:
      - `push_far`: el player empuja lo que SALE al parlante (ya a 16kHz). Va a
        un ring buffer thread-safe.
      - `process_near`: el thread del mic pasa su chunk; se procesa en bloques
        de N alineados contra el far-end del ring (zeros si falta), y devuelve
        el audio limpio en int16.
    """

    def __init__(self, block: int = 512, partitions: int = 8, mu: float = 0.3) -> None:
        self.N = block
        self._aec = EchoCanceller(block=block, partitions=partitions, mu=mu)
        self._lock = threading.Lock()
        self._far = np.zeros(0, dtype=np.float32)   # ring de far-end (16k)
        self._near_buf = np.zeros(0, dtype=np.float32)  # near pendiente < N
        self._max_far = 16000 * 2  # cap del ring (~2s) para no crecer sin fin
        self.last_erle_db = 0.0

    def reset(self) -> None:
        with self._lock:
            self._far = np.zeros(0, dtype=np.float32)
        self._near_buf = np.zeros(0, dtype=np.float32)
        self._aec.reset()
        self.last_erle_db = 0.0

    def push_far(self, samples_16k: np.ndarray) -> None:
        """El player empuja lo que reproduce (float32 16kHz mono)."""
        with self._lock:
            self._far = np.concatenate([self._far, samples_16k])
            if len(self._far) > self._max_far:
                self._far = self._far[-self._max_far:]

    def _pull_far(self, n: int) -> np.ndarray:
        """Saca n muestras de far-end del ring (zeros si no alcanza)."""
        with self._lock:
            if len(self._far) >= n:
                out = self._far[:n]
                self._far = self._far[n:]
                return out
            out = np.zeros(n, dtype=np.float32)
            out[: len(self._far)] = self._far
            self._far = np.zeros(0, dtype=np.float32)
            return out

    def process_near(self, near_bytes: bytes) -> bytes:
        """Cancela el eco del chunk de mic (int16 16k) -> bytes int16 limpios."""
        if not near_bytes:
            return near_bytes
        near = np.frombuffer(near_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._near_buf = np.concatenate([self._near_buf, near])

        cleaned_blocks: list[np.ndarray] = []
        peak_erle = 0.0
        while len(self._near_buf) >= self.N:
            nb = self._near_buf[: self.N]
            self._near_buf = self._near_buf[self.N:]
            fb = self._pull_far(self.N)
            cleaned = self._aec.process(nb, fb)
            cleaned_blocks.append(cleaned)
            peak_erle = max(peak_erle, self._aec.last_erle_db)

        if not cleaned_blocks:
            # No alcanzo un bloque completo: devolver el near tal cual (sin
            # procesar) para no romper el stream del detector.
            return near_bytes
        self.last_erle_db = peak_erle
        out = np.concatenate(cleaned_blocks)
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


# Smoke/self-test offline: eco sintetico, mide ERLE (sin hardware).
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    rng = np.random.default_rng(0)
    fs, secs = 16000, 6
    n = fs * secs

    # Far-end: ruido tipo voz (filtrado paso-bajo simple).
    far = rng.standard_normal(n).astype(np.float64)
    far = np.convolve(far, np.ones(8) / 8, mode="same")
    far /= np.max(np.abs(far))

    # Camino de eco: retardo + decaimiento (respuesta impulso sintetica).
    delay = 120  # muestras (~7.5ms)
    ir = np.zeros(400)
    ir[delay] = 0.8
    ir[delay + 40] = 0.4
    ir[delay + 120] = 0.2
    ir *= np.exp(-np.arange(400) / 150.0)
    echo = np.convolve(far, ir, mode="full")[:n]

    near = echo.copy()  # solo eco (sin voz cercana): mide ERLE puro

    aec = EchoCanceller(block=512, partitions=8, mu=0.4)
    N = 512
    cleaned = np.zeros(n, dtype=np.float64)
    for i in range(0, n - N, N):
        cleaned[i : i + N] = aec.process(near[i : i + N], far[i : i + N])

    # ERLE en la segunda mitad (ya convergido).
    half = n // 2
    erle = 10 * np.log10(np.mean(near[half:] ** 2) / (np.mean(cleaned[half:] ** 2) + 1e-12))
    print(f"ERLE tras convergencia: {erle:.1f} dB (mayor = mas eco cancelado)")
    assert erle > 15.0, f"AEC deberia cancelar >15dB de eco lineal, dio {erle:.1f}"
    print("[OK] EchoCanceller cancela eco sintetico correctamente")
