"""Diagnostico local de camaras para Jarvis/OpenCV."""

from __future__ import annotations

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.camera import _backend_candidates, _open_cv_capture, _windows_camera_summary


def main() -> int:
    print("Windows devices:")
    print(_windows_camera_summary() or "(sin resumen)")
    print()

    backends = _backend_candidates()
    if not backends:
        print("No hay backends OpenCV disponibles.")
        return 1

    found = False
    for index in range(6):
        for backend_name, backend in backends:
            dev = None
            try:
                dev = _open_cv_capture(index, backend)
                opened = bool(dev.isOpened())
                ok = False
                shape = None
                if opened:
                    for _ in range(3):
                        ok, frame = dev.read()
                        if ok and frame is not None:
                            shape = getattr(frame, "shape", None)
                            found = True
                            break
                print(f"index={index} backend={backend_name} opened={opened} read={ok} shape={shape}")
            except Exception as exc:
                print(f"index={index} backend={backend_name} ERROR {type(exc).__name__}: {exc}")
            finally:
                if dev is not None:
                    try:
                        dev.release()
                    except Exception:
                        pass
            time.sleep(0.05)
    return 0 if found else 2


if __name__ == "__main__":
    raise SystemExit(main())
