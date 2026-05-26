"""
memory/rag.py - Index FAISS + embeddings de las notas Obsidian.

Patron lift de Interview_Copilot, adaptado para:
  - Obsidian-aware: respeta scope (memory_folder vs todo el vault)
  - Indexacion incremental por archivo (no rebuild completo en cada cambio)
  - Persistencia: FAISS index + JSON manifest con file -> chunk_ids + hashes

Modelo: sentence-transformers/all-MiniLM-L6-v2 (384-dim, CPU only)
Chunk strategy: ~200 palabras con 40 overlap, respetando saltos de parrafo
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from security.secret_filter import redact_secrets, should_skip_path

from .notes import parse_note
from .obsidian_vault import ObsidianVault

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
CHUNK_WORDS = 200
CHUNK_OVERLAP = 40


@dataclass
class Chunk:
    chunk_id: int
    rel_path: str       # path relativo al vault
    title: str
    text: str
    para_idx: int       # cual parrafo del archivo

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "rel_path": self.rel_path,
            "title": self.title,
            "text": self.text,
            "para_idx": self.para_idx,
        }


@dataclass
class SearchResult:
    chunk: Chunk
    score: float        # mayor = mas relevante (similarity de FAISS L2 invertida)


def _file_hash(path: Path) -> str:
    """SHA-256 corto del contenido del archivo."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def _chunk_text(text: str, words_per_chunk: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split por palabras con overlap, intenta respetar parrafos."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0
    for para in paragraphs:
        words = para.split()
        if buf_words + len(words) <= words_per_chunk:
            buf.append(para)
            buf_words += len(words)
        else:
            if buf:
                chunks.append("\n\n".join(buf))
            # Si el parrafo es enorme, lo cortamos
            if len(words) > words_per_chunk:
                step = words_per_chunk - overlap
                for i in range(0, len(words), step):
                    chunks.append(" ".join(words[i : i + words_per_chunk]))
                buf = []
                buf_words = 0
            else:
                buf = [para]
                buf_words = len(words)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


@dataclass
class VaultRAG:
    """Index vectorial sobre las notas .md de un vault."""

    vault: ObsidianVault
    index_dir: Path = field(default_factory=lambda: Path("data/rag"))
    model: SentenceTransformer | None = None
    index: faiss.IndexFlatL2 | None = None
    chunks: list[Chunk] = field(default_factory=list)
    manifest: dict[str, dict] = field(default_factory=dict)  # rel_path -> {hash, chunk_ids}
    next_chunk_id: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.index_dir = Path(self.index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    # ---- Lazy model load ----

    def _ensure_model(self) -> SentenceTransformer:
        if self.model is None:
            print(f"[rag] Cargando modelo {EMBEDDING_MODEL}...")
            self.model = SentenceTransformer(EMBEDDING_MODEL)
            print(f"[rag] Modelo listo (dim={self.model.get_sentence_embedding_dimension()})")
        return self.model

    def _ensure_index(self) -> faiss.IndexFlatL2:
        if self.index is None:
            self.index = faiss.IndexFlatL2(EMBEDDING_DIM)
        return self.index

    # ---- Persistence ----

    @property
    def index_path(self) -> Path:
        return self.index_dir / "vault.faiss"

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def chunks_path(self) -> Path:
        return self.index_dir / "chunks.json"

    def save(self) -> None:
        with self.lock:
            if self.index is not None and self.index.ntotal > 0:
                faiss.write_index(self.index, str(self.index_path))
            self.manifest_path.write_text(
                json.dumps(self.manifest, indent=2), encoding="utf-8"
            )
            self.chunks_path.write_text(
                json.dumps([c.as_dict() for c in self.chunks], ensure_ascii=False),
                encoding="utf-8",
            )

    def load(self) -> bool:
        """Carga index + chunks + manifest. Devuelve True si carga ok."""
        if not (self.index_path.exists() and self.manifest_path.exists() and self.chunks_path.exists()):
            return False
        try:
            with self.lock:
                self.index = faiss.read_index(str(self.index_path))
                self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                chunk_dicts = json.loads(self.chunks_path.read_text(encoding="utf-8"))
                self.chunks = [Chunk(**c) for c in chunk_dicts]
                self.next_chunk_id = max((c.chunk_id for c in self.chunks), default=-1) + 1
            return True
        except Exception as exc:
            print(f"[rag] load fallo: {exc}, indexare desde cero")
            return False

    # ---- Indexing ----

    def _embed(self, texts: list[str]) -> np.ndarray:
        model = self._ensure_model()
        emb = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return emb.astype(np.float32)

    def index_file(self, path: Path) -> int:
        """Indexa o re-indexa un archivo. Devuelve cantidad de chunks creados."""
        with self.lock:
            rel = str(path.relative_to(self.vault.vault_path))
            if should_skip_path(path):
                self.remove_file(path)
                print(f"[rag] skip secreto/sensible: {rel}")
                return 0
            try:
                text = redact_secrets(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[rag] no pude leer {rel}: {exc}")
                return 0
            note = parse_note(text, path)
            h = _file_hash(path)
            existing = self.manifest.get(rel)
            if existing and existing.get("hash") == h:
                return 0  # No cambios
            # Si existia, removemos sus chunks viejos primero
            if existing:
                self._remove_chunk_ids(existing.get("chunk_ids", []))
            # Chunkear y embeber
            body_chunks = _chunk_text(note.body)
            if not body_chunks:
                self.manifest.pop(rel, None)
                return 0
            new_chunks: list[Chunk] = []
            for i, txt in enumerate(body_chunks):
                cid = self.next_chunk_id
                self.next_chunk_id += 1
                new_chunks.append(Chunk(
                    chunk_id=cid, rel_path=rel,
                    title=note.title, text=txt, para_idx=i,
                ))
            emb = self._embed([c.text for c in new_chunks])
            idx = self._ensure_index()
            idx.add(emb)
            self.chunks.extend(new_chunks)
            self.manifest[rel] = {
                "hash": h,
                "chunk_ids": [c.chunk_id for c in new_chunks],
            }
            return len(new_chunks)

    def _remove_chunk_ids(self, chunk_ids: list[int]) -> None:
        """FAISS IndexFlatL2 no soporta delete eficiente.
        Marcamos chunks como tombstone (text vacio) para excluirlos en search.
        En reindex completo ocasional limpiamos.
        """
        ids_set = set(chunk_ids)
        with self.lock:
            for c in self.chunks:
                if c.chunk_id in ids_set:
                    c.text = ""  # tombstone

    def remove_file(self, path: Path) -> int:
        with self.lock:
            rel = str(path.relative_to(self.vault.vault_path))
            if rel not in self.manifest:
                return 0
            cids = self.manifest[rel].get("chunk_ids", [])
            self._remove_chunk_ids(cids)
            del self.manifest[rel]
            return len(cids)

    def reindex_all(self, scope: str = "all") -> dict:
        """Indexa todos los .md del scope. Devuelve estadisticas."""
        files = self.vault.list_md_files(scope=scope)
        added, unchanged = 0, 0
        for f in files:
            n = self.index_file(f)
            if n > 0:
                added += n
            else:
                unchanged += 1
        return {
            "files_total": len(files),
            "files_unchanged": unchanged,
            "chunks_added": added,
            "chunks_total": sum(1 for c in self.chunks if c.text),
        }

    # ---- Search ----

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Busca semanticamente. Excluye tombstones."""
        with self.lock:
            idx = self._ensure_index()
            if idx.ntotal == 0 or not self.chunks:
                return []
            q_emb = self._embed([query])
            # Pedimos bastante mas que top_k porque los updates dejan tombstones.
            live_chunks = max(1, sum(1 for c in self.chunks if c.text))
            k_search = min(max(top_k * 10, top_k), idx.ntotal, live_chunks + top_k * 4)
            D, I = idx.search(q_emb, k_search)
            results: list[SearchResult] = []
            for rank, faiss_idx in enumerate(I[0]):
                if faiss_idx < 0 or faiss_idx >= len(self.chunks):
                    continue
                chunk = self.chunks[faiss_idx]
                if not chunk.text:  # tombstone
                    continue
                # Convertir distancia L2 a un score 0..1 (heuristica simple)
                distance = float(D[0][rank])
                score = 1.0 / (1.0 + distance)
                results.append(SearchResult(chunk=chunk, score=score))
                if len(results) >= top_k:
                    break
            return results


# Smoke test
if __name__ == "__main__":
    import sys
    import time
    from pathlib import Path as _P

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env")

    v = ObsidianVault()
    rag = VaultRAG(vault=v, index_dir=_P("data/rag"))

    # Cargar si existe, sino desde cero
    if rag.load():
        print(f"[OK] Index cargado: {len(rag.chunks)} chunks de {len(rag.manifest)} archivos")
    else:
        print("[INFO] Sin index previo. Indexando vault completo (puede tardar 1-3 min en cold start)...")
        t0 = time.perf_counter()
        stats = rag.reindex_all(scope="all")
        elapsed = time.perf_counter() - t0
        print(f"[OK] Indexado en {elapsed:.1f}s: {stats}")
        rag.save()
        print(f"[OK] Persistido en {rag.index_path.parent}")

    # Probar busqueda
    queries = [
        "que es speech-to-speech",
        "Aoede voz",
        "drone Betaflight CLI",
        "agentics aws lambda",
    ]
    print("\n=== Busquedas ===")
    for q in queries:
        results = rag.search(q, top_k=2)
        print(f"\nQ: {q!r}")
        if not results:
            print("   (sin resultados)")
        for r in results:
            preview = r.chunk.text[:120].replace("\n", " ")
            print(f"   [{r.score:.3f}] {r.chunk.title}  -> {preview}...")

    print("\n[OK] VaultRAG smoke test passed")
