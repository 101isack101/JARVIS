"""
memory/indexer.py - Watchdog del vault con reindex incremental.

Mantiene el VaultRAG sincronizado con cambios en el vault de Obsidian:
  - Nota nueva creada -> indexar
  - Nota modificada (Isaac edito o Jarvis escribio) -> re-indexar
  - Nota borrada -> remover del index

Usa watchdog.observers.Observer en background. Aplica debounce (espera 2s
de quietud) antes de re-indexar para evitar thrashing cuando se guarda
varias veces seguidas (Obsidian suele guardar al cambiar de pestaña).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from security.secret_filter import should_skip_path

from .rag import VaultRAG

DEBOUNCE_SECONDS = 2.0
PERSIST_AFTER_N_CHANGES = 5  # cada N reindex, persistir a disco


class _MdEventHandler(FileSystemEventHandler):
    """Forwarder de eventos a una Queue para procesamiento en otro thread."""

    def __init__(self, queue: Queue) -> None:
        super().__init__()
        self.queue = queue

    def _maybe_enqueue(self, event: FileSystemEvent, kind: str) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".md":
            return
        # Excluir paths internos de Obsidian
        if any(part.startswith(".") for part in path.parts):
            return
        if should_skip_path(path):
            return
        self.queue.put((kind, path, time.time()))

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event, "create")

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event, "modify")

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        # Tratar como delete + create
        if event.is_directory:
            return
        if hasattr(event, "src_path") and event.src_path.endswith(".md"):
            self.queue.put(("delete", Path(event.src_path), time.time()))
        if hasattr(event, "dest_path") and event.dest_path.endswith(".md"):
            self.queue.put(("create", Path(event.dest_path), time.time()))


class IncrementalIndexer:
    """Watcher de vault + worker que aplica cambios al VaultRAG."""

    def __init__(
        self,
        rag: VaultRAG,
        on_change: Callable[[str, Path, int], None] | None = None,
    ) -> None:
        self.rag = rag
        self.queue: Queue = Queue()
        self.observer: Observer | None = None
        self.worker_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_change = on_change or (lambda kind, path, n: None)
        self._changes_since_save = 0

    def start(self) -> None:
        if self.observer is not None:
            return
        watch_path = self.rag.vault.vault_path
        handler = _MdEventHandler(self.queue)
        self.observer = Observer()
        self.observer.schedule(handler, str(watch_path), recursive=True)
        self.observer.start()
        self.worker_thread = threading.Thread(
            target=self._worker, name="JarvisVaultIndexer", daemon=True
        )
        self.worker_thread.start()
        print(f"[indexer] Watcher iniciado en {watch_path}")

    def stop(self) -> None:
        self._stop.set()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2.0)
            self.observer = None
        if self.worker_thread:
            self.worker_thread.join(timeout=3.0)
            self.worker_thread = None
        # Flush pendientes
        if self._changes_since_save > 0:
            self.rag.save()

    def _worker(self) -> None:
        """Toma eventos de la cola, debounce, aplica al RAG."""
        pending: dict[Path, tuple[str, float]] = {}  # path -> (kind, last_event_ts)
        while not self._stop.is_set():
            try:
                kind, path, ts = self.queue.get(timeout=0.5)
                pending[path] = (kind, ts)
            except Empty:
                pass

            now = time.time()
            ready = [p for p, (k, t) in pending.items() if now - t >= DEBOUNCE_SECONDS]
            for path in ready:
                kind, _ = pending.pop(path)
                try:
                    self._apply(kind, path)
                except Exception as exc:
                    print(f"[indexer] error procesando {kind} {path}: {exc}")

            if self._changes_since_save >= PERSIST_AFTER_N_CHANGES:
                self.rag.save()
                self._changes_since_save = 0

    def _apply(self, kind: str, path: Path) -> None:
        if kind in ("create", "modify"):
            # Validar que sigue existiendo (puede haber sido borrada antes del debounce)
            if not path.exists():
                return
            # Validar scope (no indexar fuera del vault)
            try:
                self.rag.vault.assert_readable(path)
            except Exception as e:
                print(f"[indexer] skip {path}: {e}")
                return
            if should_skip_path(path):
                self.rag.remove_file(path)
                print(f"[indexer] skip secreto/sensible: {path}")
                return
            n_chunks = self.rag.index_file(path)
            if n_chunks > 0:
                self._changes_since_save += 1
                self._on_change(kind, path, n_chunks)
        elif kind == "delete":
            n = self.rag.remove_file(path)
            if n > 0:
                self._changes_since_save += 1
                self._on_change(kind, path, n)


# Smoke test: crea archivo, espera, verifica que el indexer lo recoja
if __name__ == "__main__":
    import sys
    from pathlib import Path as _P

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env")

    from .obsidian_vault import ObsidianVault
    from .rag import VaultRAG

    v = ObsidianVault()
    rag = VaultRAG(vault=v, index_dir=_P("data/rag"))
    if not rag.load():
        print("[INFO] index no cargado, indexando vault completo...")
        rag.reindex_all()
        rag.save()
    print(f"[OK] {len(rag.chunks)} chunks cargados")

    events_seen = []

    def on_change(kind, path, n_chunks):
        rel = path.relative_to(v.vault_path)
        events_seen.append((kind, rel, n_chunks))
        print(f"  [event] {kind:6s} {rel} (+{n_chunks} chunks)")

    indexer = IncrementalIndexer(rag, on_change=on_change)
    indexer.start()

    test_path = v.memory_file("Indexer test note")
    print(f"\nCreando: {test_path.relative_to(v.vault_path)}")
    test_path.write_text(
        "# Indexer test\n\nEste es contenido inicial para probar el watcher.",
        encoding="utf-8",
    )

    print("Esperando 4s para debounce + procesamiento...")
    time.sleep(4)

    print(f"\nModificando: {test_path.relative_to(v.vault_path)}")
    test_path.write_text(
        "# Indexer test\n\nContenido NUEVO para verificar el reindex.",
        encoding="utf-8",
    )
    time.sleep(4)

    # Search del nuevo contenido
    res = rag.search("contenido nuevo verificar reindex", top_k=2)
    print(f"\nSearch results para 'contenido nuevo':")
    for r in res:
        print(f"  [{r.score:.3f}] {r.chunk.title} -> {r.chunk.text[:60]}...")

    print(f"\nBorrando: {test_path.relative_to(v.vault_path)}")
    test_path.unlink()
    time.sleep(4)

    indexer.stop()
    print(f"\n[OK] Eventos detectados: {len(events_seen)}")
    for ev in events_seen:
        print(f"  - {ev}")
    print("[OK] IncrementalIndexer smoke test passed")
