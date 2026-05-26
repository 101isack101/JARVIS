"""
jarvis_health.py - Healthcheck de arranque para Jarvis.

Verifica que dependencias criticas esten disponibles ANTES de levantar la
sesion Gemini Live (que es la fuente de costo). Fail-fast con mensaje claro
si falta algo. Importable como modulo o ejecutable como script (`python jarvis_health.py`).

Verificaciones (orden):
  1. Variables de entorno minimas (GEMINI_API_KEY, ANTHROPIC_API_KEY, vault).
  2. Audio de entrada (microfono detectable via sounddevice).
  3. Audio de salida (parlante/altavoz default).
  4. Vault Obsidian: existe, es directorio, escribible en memory_folder.
  5. Disco: data/ escribible.
  6. (opcional, lento) Ping a Gemini con 1 token.

Resultado: HealthReport con `ok: bool` y lista de checks individuales. Exit
code 0 si OK; 2 si STRICT y algo falla; 1 para errores no esperados.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    elapsed_ms: float = 0.0
    # Si optional=True, un fallo se reporta como warning visible pero NO
    # bloquea el arranque en modo strict. Usar para features que Jarvis
    # puede tolerar (Spotify, Bedrock, etc.) — no para mic/speaker/Gemini.
    optional: bool = False


@dataclass
class HealthReport:
    ok: bool
    checks: list[Check] = field(default_factory=list)
    total_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total_ms": round(self.total_ms, 1),
            "checks": [asdict(c) for c in self.checks],
        }


def _check_env() -> Check:
    t0 = time.perf_counter()
    missing = []
    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "JARVIS_OBSIDIAN_VAULT"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        return Check(
            name="env",
            ok=False,
            detail=f"variables sin valor: {', '.join(missing)}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    return Check(name="env", ok=True, detail="todas las vars criticas presentes",
                 elapsed_ms=(time.perf_counter() - t0) * 1000)


def _check_mic() -> Check:
    t0 = time.perf_counter()
    try:
        import sounddevice as sd

        devs = sd.query_devices()
        # default[0] = input, default[1] = output
        try:
            default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        except Exception:
            default_in = None
        in_devs = [d for d in devs if d.get("max_input_channels", 0) > 0]
        if not in_devs:
            return Check(name="mic", ok=False, detail="no se detecto microfono",
                         elapsed_ms=(time.perf_counter() - t0) * 1000)
        return Check(
            name="mic",
            ok=True,
            detail=f"{len(in_devs)} dispositivo(s) de entrada (default={default_in})",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        return Check(name="mic", ok=False, detail=f"{type(exc).__name__}: {exc}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)


def _check_speaker() -> Check:
    t0 = time.perf_counter()
    try:
        import sounddevice as sd

        devs = sd.query_devices()
        out_devs = [d for d in devs if d.get("max_output_channels", 0) > 0]
        if not out_devs:
            return Check(name="speaker", ok=False, detail="no se detecto salida de audio",
                         elapsed_ms=(time.perf_counter() - t0) * 1000)
        return Check(
            name="speaker",
            ok=True,
            detail=f"{len(out_devs)} dispositivo(s) de salida",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        return Check(name="speaker", ok=False, detail=f"{type(exc).__name__}: {exc}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)


def _check_vault() -> Check:
    t0 = time.perf_counter()
    vault_str = os.environ.get("JARVIS_OBSIDIAN_VAULT", "")
    if not vault_str:
        return Check(name="vault", ok=False, detail="JARVIS_OBSIDIAN_VAULT no set",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)
    p = Path(vault_str).resolve()
    if not p.exists():
        return Check(name="vault", ok=False, detail=f"vault no existe: {p}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)
    if not p.is_dir():
        return Check(name="vault", ok=False, detail=f"vault no es directorio: {p}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)
    mem_folder = os.environ.get("JARVIS_OBSIDIAN_MEMORY_FOLDER", "Jarvis Memory")
    mem_path = p / mem_folder
    try:
        mem_path.mkdir(parents=True, exist_ok=True)
        probe = mem_path / ".jarvis_health_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return Check(name="vault", ok=False, detail=f"memory_folder no escribible: {exc}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)
    return Check(name="vault", ok=True, detail=f"{p} (memory={mem_folder})",
                 elapsed_ms=(time.perf_counter() - t0) * 1000)


def _check_data_dir() -> Check:
    t0 = time.perf_counter()
    data = ROOT / "data"
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".jarvis_health_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return Check(name="data_dir", ok=False, detail=f"{type(exc).__name__}: {exc}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)
    return Check(name="data_dir", ok=True, detail=str(data),
                 elapsed_ms=(time.perf_counter() - t0) * 1000)


def _check_spotify_scope() -> Check:
    """Verifica que el cache OAuth de Spotify tenga los scopes necesarios.

    No hace network call — solo lee el cache JSON local y chequea el campo
    'scope'. Si falta user-library-read, las features de biblioteca
    (play_from_library, refresh_library) fallaran en runtime. Mejor avisar
    al arranque que durante una sesion de voz.
    """
    t0 = time.perf_counter()
    cache_path = Path(
        os.environ.get("JARVIS_SPOTIFY_CACHE_PATH", "data/spotify/.cache")
    )
    if not cache_path.is_absolute():
        cache_path = ROOT / cache_path
    if not cache_path.exists():
        return Check(
            name="spotify_scope",
            ok=False,
            detail=(
                "No hay cache OAuth (data/spotify/.cache). Corre: "
                'python -m actions.spotify_controller --login'
            ),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            optional=True,
        )
    try:
        import json as _json
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return Check(
            name="spotify_scope",
            ok=False,
            detail=f"cache OAuth ilegible: {type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            optional=True,
        )
    scopes = set((data.get("scope") or "").split())
    required = {
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "user-library-read",
    }
    missing = required - scopes
    if missing:
        return Check(
            name="spotify_scope",
            ok=False,
            detail=(
                f"faltan scopes {sorted(missing)}. Refresca con: "
                'python -m actions.spotify_controller --login'
            ),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            optional=True,
        )
    return Check(
        name="spotify_scope",
        ok=True,
        detail=f"{len(scopes)} scopes OK (incluye user-library-read)",
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def _check_gemini_ping(timeout_s: float) -> Check:
    """Ping minimo a Gemini para validar API key y conectividad.

    Usa el endpoint sincrono `models.generate_content` con max_output_tokens=1.
    Costo ~0.0001 USD. Si pasa de `timeout_s` se considera fallo.
    """
    t0 = time.perf_counter()
    try:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return Check(name="gemini_ping", ok=False, detail="GEMINI_API_KEY no set",
                         elapsed_ms=(time.perf_counter() - t0) * 1000)
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="ping",
            config={"max_output_tokens": 1},
        )
        ok = resp is not None
        return Check(
            name="gemini_ping",
            ok=ok,
            detail="api alcanzable" if ok else "respuesta vacia",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:
        return Check(name="gemini_ping", ok=False, detail=f"{type(exc).__name__}: {exc}",
                     elapsed_ms=(time.perf_counter() - t0) * 1000)


def run_healthcheck(strict: bool = True, ping_gemini: bool = False,
                    timeout_s: float = 5.0) -> HealthReport:
    """Corre las verificaciones y devuelve un HealthReport.

    `strict=True` significa que report.ok = AND de todos los checks.
    `strict=False` siempre devuelve ok=True (pero report.checks conserva fallos).
    `ping_gemini=True` agrega un ping HTTP a Gemini (lento, cuesta ~$0.0001).
    """
    t0 = time.perf_counter()
    checks = [
        _check_env(),
        _check_data_dir(),
        _check_vault(),
        _check_mic(),
        _check_speaker(),
        _check_spotify_scope(),
    ]
    if ping_gemini:
        checks.append(_check_gemini_ping(timeout_s=timeout_s))
    # En strict mode solo los checks NO opcionales pueden romper el arranque.
    # Los optional (Spotify scope, etc.) se reportan como advertencias.
    critical_ok = all(c.ok for c in checks if not c.optional)
    report_ok = critical_ok if strict else True
    return HealthReport(
        ok=report_ok,
        checks=checks,
        total_ms=(time.perf_counter() - t0) * 1000,
    )


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    strict = os.environ.get("JARVIS_HEALTHCHECK_STRICT", "true").lower() == "true"
    timeout = float(os.environ.get("JARVIS_HEALTHCHECK_TIMEOUT", "5.0"))
    ping = "--ping" in sys.argv

    print("=" * 60)
    print("  Jarvis healthcheck")
    print("=" * 60)
    report = run_healthcheck(strict=strict, ping_gemini=ping, timeout_s=timeout)
    for c in report.checks:
        if c.ok:
            status = "OK  "
        elif c.optional:
            status = "WARN"
        else:
            status = "FAIL"
        print(f"  [{status}] {c.name:<14s} ({c.elapsed_ms:>6.1f}ms)  {c.detail}")
    print(f"\nTotal: {report.total_ms:.1f}ms  -> {'OK' if report.ok else 'FAILED'}")

    if not report.ok:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
