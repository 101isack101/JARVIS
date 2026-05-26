"""
memory/obsidian_vault.py - Wrapper del vault Obsidian de Isaac.

Funciones:
  - Detectar el vault activo via %APPDATA%\\obsidian\\obsidian.json
  - Resolver paths absolutos dentro del vault
  - Garantizar que la carpeta de memoria exista (ej. "Jarvis Memory/")
  - Validar que una path este DENTRO del vault y dentro del scope permitido
    (escrituras solo en memory_folder, lecturas en TODO el vault si READ_ALL)

Es la unica capa que sabe del filesystem real del vault. El resto del paquete
opera con paths relativos al vault.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from security.secret_filter import should_skip_path

# Default vault para Isaac segun memoria feedback_obsidian_active_vault.md.
# Nota: usamos backslash escapado para coherencia con el `.env` y para evitar
# confusion al comparar paths como string. `Path()` normaliza ambos en
# Windows, asi que el separador real lo decide Pathlib en runtime.
DEFAULT_VAULT = Path(r"H:\Obsidian ClaudeCode\Obsidian Claude Code")
DEFAULT_MEMORY_FOLDER = "Jarvis Memory"


class VaultError(Exception):
    """Path fuera del vault o fuera del scope permitido."""


class ObsidianVault:
    """Acceso controlado al vault de Obsidian."""

    def __init__(
        self,
        vault_path: Path | str | None = None,
        memory_folder: str | None = None,
        read_all: bool | None = None,
    ) -> None:
        self.vault_path = Path(
            vault_path
            or os.environ.get("JARVIS_OBSIDIAN_VAULT", str(DEFAULT_VAULT))
        ).resolve()

        if not self.vault_path.exists():
            raise VaultError(f"Vault no existe: {self.vault_path}")
        if not self.vault_path.is_dir():
            raise VaultError(f"Vault no es directorio: {self.vault_path}")

        self.memory_folder = (
            memory_folder
            or os.environ.get("JARVIS_OBSIDIAN_MEMORY_FOLDER", DEFAULT_MEMORY_FOLDER)
        )
        self.memory_path = (self.vault_path / self.memory_folder).resolve()
        self.memory_path.mkdir(parents=True, exist_ok=True)

        # Leer scope: 'true' (lee todo el vault) o 'false' (solo memory_folder)
        if read_all is not None:
            self.read_all = read_all
        else:
            self.read_all = (
                os.environ.get("JARVIS_OBSIDIAN_READ_ALL", "true").lower() == "true"
            )

    # ---- Detection ----

    @staticmethod
    def detect_active_vault() -> Path | None:
        """Lee %APPDATA%\\obsidian\\obsidian.json y devuelve el vault activo."""
        cfg = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
        if not cfg.exists():
            return None
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            return None
        vaults = data.get("vaults", {})
        # Buscar el que tenga "open": true
        for vid, info in vaults.items():
            if info.get("open"):
                return Path(info.get("path", ""))
        # Fallback: primer vault registrado
        for vid, info in vaults.items():
            return Path(info.get("path", ""))
        return None

    # ---- Path validation ----

    def _is_inside(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def assert_writable(self, path: Path) -> None:
        """Lanza VaultError si path no esta dentro de memory_folder."""
        if not self._is_inside(path, self.memory_path):
            raise VaultError(
                f"Escritura denegada: {path} fuera de {self.memory_path}"
            )

    def assert_readable(self, path: Path) -> None:
        """Lanza VaultError si path esta fuera del vault o de scope."""
        if not self._is_inside(path, self.vault_path):
            raise VaultError(f"Lectura denegada: {path} fuera del vault")
        if not self.read_all and not self._is_inside(path, self.memory_path):
            raise VaultError(
                f"Lectura denegada: read_all=false y {path} fuera de memoria"
            )

    # ---- Path helpers ----

    def memory_file(self, name: str) -> Path:
        """Resuelve un path dentro de memory_folder. Crea sub-dirs si hace falta."""
        if not name.endswith(".md"):
            name = name + ".md"
        # Saneo basico para nombres de archivo en Windows
        for bad in '<>:"/\\|?*':
            name = name.replace(bad, "_")
        path = self.memory_path / name
        return path

    def list_md_files(self, scope: str = "memory") -> list[Path]:
        """Lista .md files. scope='memory' | 'all' (respeta read_all)."""
        if scope == "memory":
            root = self.memory_path
        elif scope == "all":
            if not self.read_all:
                root = self.memory_path
            else:
                root = self.vault_path
        else:
            raise ValueError(f"scope invalido: {scope}")
        # Excluir .obsidian/ y .trash/
        files = []
        for p in root.rglob("*.md"):
            parts = p.relative_to(self.vault_path).parts
            if any(part.startswith(".") for part in parts):
                continue
            if should_skip_path(p):
                continue
            files.append(p)
        return sorted(files)

    def __repr__(self) -> str:
        return (
            f"ObsidianVault(vault={self.vault_path!s}, "
            f"memory={self.memory_folder!r}, read_all={self.read_all})"
        )


# Smoke test
if __name__ == "__main__":
    import sys
    from pathlib import Path as _P

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env")

    detected = ObsidianVault.detect_active_vault()
    print(f"Vault detectado por obsidian.json: {detected}")

    v = ObsidianVault()
    print(f"Vault activo:     {v.vault_path}")
    print(f"Memory folder:    {v.memory_path}")
    print(f"Read all:         {v.read_all}")

    # Listar notas
    mem_notes = v.list_md_files(scope="memory")
    all_notes = v.list_md_files(scope="all")
    print(f"\nNotas en memoria:  {len(mem_notes)}")
    print(f"Notas totales:     {len(all_notes)}")
    if mem_notes:
        print("Primeras notas en memoria:")
        for p in mem_notes[:3]:
            print(f"  {p.relative_to(v.vault_path)}")

    # Test guardrails
    test_path_ok = v.memory_file("Test note")
    print(f"\nPath OK escritura: {test_path_ok.relative_to(v.vault_path)}")
    v.assert_writable(test_path_ok)
    print("[OK] assert_writable paso")

    try:
        outside = v.vault_path / "NotaIsaac.md"
        v.assert_writable(outside)
        print("[FAIL] deberia haber bloqueado escritura fuera de memory_folder")
    except VaultError as e:
        print(f"[OK] Bloqueo esperado: {e}")

    print("\n[OK] ObsidianVault smoke test passed")
