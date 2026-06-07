"""Multi-source semantic memory for Jarvis.

This module keeps the same search shape as ``VaultRAG`` while expanding the
corpus beyond Obsidian: curated agent memories, summarized agent histories and
safe local project docs. Raw conversation logs are never indexed directly; they
are converted into compact, redacted extractive summaries first.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from security.policy import is_inside_root, is_secret_path
from security.secret_filter import redact_secrets, should_skip_path

from . import notes as notes_mod
from .obsidian_vault import ObsidianVault
from .rag import EMBEDDING_DIM, EMBEDDING_MODEL, _chunk_text
from .triage import detect_project

DEFAULT_SOURCES = (
    "obsidian",
    "claude_memory",
    "codex_memory",
    "agent_history_summaries",
    "project_docs",
)
DEFAULT_MIN_SCORE = 0.32
PROJECT_DOC_EXTENSIONS = {".md", ".markdown", ".txt"}
PROJECT_DOC_NAMES = {"readme", "readme.md"}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "data",
    "logs",
    "dist",
    "build",
    "coverage",
}
MAX_HISTORY_TEXT_CHARS = 14_000
MAX_HISTORY_LINES = 20_000
MAX_HISTORY_FILES = 120


@dataclass(frozen=True)
class SourceDocument:
    source_type: str
    source_uri: str
    title: str
    text: str
    path: str = ""
    date: str | None = None
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: str = "medium"
    metadata: dict = field(default_factory=dict)


@dataclass
class MemoryChunk:
    chunk_id: int
    source_type: str
    source_uri: str
    rel_path: str
    title: str
    text: str
    para_idx: int = 0
    date: str | None = None
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: str = "medium"
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "rel_path": self.rel_path,
            "title": self.title,
            "text": self.text,
            "para_idx": self.para_idx,
            "date": self.date,
            "project": self.project,
            "tags": self.tags,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class SemanticSearchResult:
    chunk: MemoryChunk
    score: float


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_sources(value: str | None = None) -> tuple[str, ...]:
    raw = value or os.environ.get("JARVIS_SEMANTIC_SOURCES", ",".join(DEFAULT_SOURCES))
    items = tuple(item.strip() for item in raw.split(",") if item.strip())
    return items or DEFAULT_SOURCES


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def _path_has_excluded_part(path: Path) -> bool:
    return any(part.lower() in EXCLUDED_PARTS or part.startswith(".") for part in path.parts)


def _safe_project_doc(path: Path, root: Path) -> bool:
    if not path.is_file() or not is_inside_root(path, root):
        return False
    if _path_has_excluded_part(path.relative_to(root)):
        return False
    if is_secret_path(path):
        return False
    name = path.name.lower()
    return path.suffix.lower() in PROJECT_DOC_EXTENSIONS or name in PROJECT_DOC_NAMES


def _extract_text_values(value, out: list[str], *, depth: int = 0) -> None:
    if depth > 6 or len(" ".join(out)) > MAX_HISTORY_TEXT_CHARS:
        return
    if isinstance(value, str):
        clean = " ".join(value.split())
        if 24 <= len(clean) <= 3000 and not clean.startswith("data:"):
            out.append(clean)
        return
    if isinstance(value, list):
        for item in value[:80]:
            _extract_text_values(item, out, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in {"text", "content", "message", "summary", "prompt", "response"}:
                _extract_text_values(item, out, depth=depth + 1)
            elif isinstance(item, (dict, list)):
                _extract_text_values(item, out, depth=depth + 1)


def summarize_jsonl_history(path: Path, *, max_chars: int = MAX_HISTORY_TEXT_CHARS) -> str:
    """Build a compact redacted extractive summary from an agent JSONL file."""
    snippets: list[str] = []
    line_count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_count += 1
                if line_count > MAX_HISTORY_LINES:
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _extract_text_values(payload, snippets)
                if len("\n".join(snippets)) >= max_chars:
                    break
    except Exception:
        return ""
    seen: set[str] = set()
    kept: list[str] = []
    for item in snippets:
        redacted = redact_secrets(item)
        key = redacted[:160].lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(redacted)
        if len("\n".join(kept)) >= max_chars:
            break
    if not kept:
        return ""
    bullets = "\n".join(f"- {item[:500]}" for item in kept[:24])
    return (
        f"## Resumen extractivo\n{bullets}\n\n"
        f"## Metadata\n- source_file: {path.name}\n- sampled_lines: {line_count}\n"
    )


class SourceRegistry:
    """Discovers safe local memory documents for the semantic index."""

    def __init__(
        self,
        *,
        vault: ObsidianVault,
        workspace_root: Path | str | None = None,
        sources: Iterable[str] | None = None,
        claude_memory_dir: Path | str | None = None,
        codex_memory_dir: Path | str | None = None,
        claude_history_dir: Path | str | None = None,
        codex_sessions_dir: Path | str | None = None,
    ) -> None:
        self.vault = vault
        self.workspace_root = Path(
            workspace_root
            or os.environ.get("JARVIS_WORKSPACE_ROOT", str(Path(__file__).resolve().parents[2]))
        ).resolve()
        self.sources = tuple(sources or _split_sources())
        self.claude_memory_dir = Path(
            claude_memory_dir
            or os.environ.get(
                "JARVIS_CLAUDE_MEMORY_DIR",
                r"C:\Users\Isaac\.claude\projects\c--Users-Isaac-Desktop-PROYECTOS\memory",
            )
        )
        self.codex_memory_dir = Path(
            codex_memory_dir or os.environ.get("JARVIS_CODEX_MEMORY_DIR", r"C:\Users\Isaac\.codex\memories")
        )
        self.claude_history_dir = Path(
            claude_history_dir
            or os.environ.get(
                "JARVIS_CLAUDE_HISTORY_DIR",
                r"C:\Users\Isaac\.claude\projects\c--Users-Isaac-Desktop-PROYECTOS",
            )
        )
        self.codex_sessions_dir = Path(
            codex_sessions_dir or os.environ.get("JARVIS_CODEX_SESSIONS_DIR", r"C:\Users\Isaac\.codex\sessions")
        )

    def iter_documents(self) -> Iterable[SourceDocument]:
        if "obsidian" in self.sources:
            yield from self._iter_obsidian()
        if "claude_memory" in self.sources:
            yield from self._iter_memory_dir(self.claude_memory_dir, "claude_memory")
        if "codex_memory" in self.sources:
            yield from self._iter_memory_dir(self.codex_memory_dir, "codex_memory")
        if "agent_history_summaries" in self.sources:
            yield from self._iter_history_summaries()
        if "project_docs" in self.sources:
            yield from self._iter_project_docs()

    def _iter_obsidian(self) -> Iterable[SourceDocument]:
        for path in self.vault.list_md_files(scope="all"):
            try:
                note = notes_mod.read_note(self.vault, path)
            except Exception:
                continue
            rel = _safe_rel(path, self.vault.vault_path)
            norm_rel = rel.replace("\\", "/")
            source_type = "jarvis_session" if "/sessions/" in f"/{norm_rel}/" else "obsidian"
            body = redact_secrets(note.body or "")
            if not body.strip():
                continue
            yield SourceDocument(
                source_type=source_type,
                source_uri=f"{source_type}:{rel}",
                path=rel,
                title=note.title or path.stem,
                text=body,
                date=str(note.frontmatter.get("date") or note.frontmatter.get("updated") or ""),
                project=str(note.frontmatter.get("project") or detect_project(body) or ""),
                tags=list(note.tags),
                confidence=str(note.frontmatter.get("confidence") or "medium"),
                metadata={"frontmatter_type": note.frontmatter.get("type", "")},
            )

    def _iter_memory_dir(self, root: Path, source_type: str) -> Iterable[SourceDocument]:
        if not root.exists() or not root.is_dir():
            return
        for path in sorted(root.glob("*.md")):
            if should_skip_path(path):
                continue
            try:
                text = redact_secrets(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not text.strip():
                continue
            rel = path.name
            yield SourceDocument(
                source_type=source_type,
                source_uri=f"{source_type}:{rel}",
                path=rel,
                title=path.stem.replace("_", " "),
                text=text,
                project=detect_project(text) or "",
                confidence="high",
            )

    def _iter_history_summaries(self) -> Iterable[SourceDocument]:
        roots = [
            ("claude_history_summary", self.claude_history_dir),
            ("codex_history_summary", self.codex_sessions_dir),
        ]
        max_files = int(os.environ.get("JARVIS_SEMANTIC_HISTORY_MAX_FILES", str(MAX_HISTORY_FILES)))
        for source_type, root in roots:
            if not root.exists():
                continue
            files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
            for path in files:
                if should_skip_path(path):
                    continue
                text = summarize_jsonl_history(path)
                if not text.strip():
                    continue
                rel = _safe_rel(path, root)
                yield SourceDocument(
                    source_type=source_type,
                    source_uri=f"{source_type}:{rel}",
                    path=rel,
                    title=f"{source_type.replace('_', ' ')} {path.stem[:8]}",
                    text=text,
                    date=_mtime_date(path),
                    project=detect_project(text) or "",
                    confidence="medium",
                    metadata={"summarized_from": "jsonl"},
                )

    def _iter_project_docs(self) -> Iterable[SourceDocument]:
        if not self.workspace_root.exists():
            return
        for path in sorted(self.workspace_root.rglob("*")):
            if not _safe_project_doc(path, self.workspace_root):
                continue
            try:
                text = redact_secrets(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not text.strip():
                continue
            rel = _safe_rel(path, self.workspace_root)
            yield SourceDocument(
                source_type="project_doc",
                source_uri=f"project_doc:{rel}",
                path=rel,
                title=path.stem,
                text=text,
                date=_mtime_date(path),
                project=detect_project(f"{rel}\n{text}") or "",
                confidence="low",
            )


def _mtime_date(path: Path) -> str:
    try:
        from datetime import datetime

        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return ""


@dataclass
class SemanticMemoryIndex:
    index_dir: Path = field(default_factory=lambda: Path("data/semantic_memory"))
    model: SentenceTransformer | None = None
    index: faiss.IndexFlatIP | None = None
    chunks: list[MemoryChunk] = field(default_factory=list)
    manifest: dict[str, dict] = field(default_factory=dict)
    next_chunk_id: int = 0
    min_score: float = DEFAULT_MIN_SCORE
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.index_dir = Path(self.index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.min_score = float(os.environ.get("JARVIS_SEMANTIC_MIN_SCORE", str(self.min_score)))

    @classmethod
    def from_env(cls, root: Path) -> "SemanticMemoryIndex":
        index_dir = Path(os.environ.get("JARVIS_SEMANTIC_INDEX_DIR", str(root / "data" / "semantic_memory")))
        return cls(index_dir=index_dir)

    @staticmethod
    def enabled() -> bool:
        return _truthy_env("JARVIS_SEMANTIC_MEMORY_ENABLED", True)

    @property
    def index_path(self) -> Path:
        return self.index_dir / "semantic.faiss"

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def chunks_path(self) -> Path:
        return self.index_dir / "chunks.json"

    def _ensure_model(self) -> SentenceTransformer:
        if self.model is None:
            self.model = SentenceTransformer(EMBEDDING_MODEL)
        return self.model

    def _ensure_index(self) -> faiss.IndexFlatIP:
        if self.index is None:
            self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
        return self.index

    def _embed(self, texts: list[str]) -> np.ndarray:
        emb = self._ensure_model().encode(texts, convert_to_numpy=True, show_progress_bar=False)
        arr = emb.astype(np.float32)
        faiss.normalize_L2(arr)
        return arr

    def load(self) -> bool:
        if not (self.index_path.exists() and self.manifest_path.exists() and self.chunks_path.exists()):
            return False
        try:
            with self.lock:
                self.index = faiss.read_index(str(self.index_path))
                self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self.chunks = [MemoryChunk(**item) for item in json.loads(self.chunks_path.read_text(encoding="utf-8"))]
                self.next_chunk_id = max((c.chunk_id for c in self.chunks), default=-1) + 1
            return True
        except Exception:
            self.clear()
            return False

    def save(self) -> None:
        with self.lock:
            if self.index is not None:
                faiss.write_index(self.index, str(self.index_path))
            self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
            self.chunks_path.write_text(
                json.dumps([chunk.as_dict() for chunk in self.chunks], ensure_ascii=False),
                encoding="utf-8",
            )

    def clear(self) -> None:
        with self.lock:
            self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self.chunks = []
            self.manifest = {}
            self.next_chunk_id = 0

    def rebuild(self, registry: SourceRegistry) -> dict:
        self.clear()
        return self.index_documents(registry.iter_documents(), reset=False)

    def index_documents(self, documents: Iterable[SourceDocument], *, reset: bool = False) -> dict:
        if reset:
            self.clear()
        docs_total = 0
        chunks_added = 0
        with self.lock:
            for doc in documents:
                docs_total += 1
                chunks_added += self._index_document_locked(doc)
        self.save()
        return {
            "documents_total": docs_total,
            "chunks_added": chunks_added,
            "chunks_total": len(self.chunks),
            "sources": sorted({chunk.source_type for chunk in self.chunks}),
        }

    def _index_document_locked(self, doc: SourceDocument) -> int:
        clean_text = redact_secrets(doc.text or "").strip()
        if not clean_text:
            return 0
        doc_hash = _sha_text(clean_text)
        existing = self.manifest.get(doc.source_uri)
        if existing and existing.get("hash") == doc_hash:
            return 0
        if existing:
            self._remove_chunk_ids(existing.get("chunk_ids", []))
        pieces = _chunk_text(clean_text, words_per_chunk=320, overlap=60)
        if not pieces:
            self.manifest.pop(doc.source_uri, None)
            return 0
        new_chunks: list[MemoryChunk] = []
        for idx, piece in enumerate(pieces):
            chunk = MemoryChunk(
                chunk_id=self.next_chunk_id,
                source_type=doc.source_type,
                source_uri=doc.source_uri,
                rel_path=doc.path or doc.source_uri,
                title=doc.title,
                text=piece,
                para_idx=idx,
                date=doc.date or None,
                project=doc.project or None,
                tags=doc.tags,
                confidence=doc.confidence,
                metadata=doc.metadata,
            )
            self.next_chunk_id += 1
            new_chunks.append(chunk)
        emb = self._embed([chunk.text for chunk in new_chunks])
        self._ensure_index().add(emb)
        self.chunks.extend(new_chunks)
        self.manifest[doc.source_uri] = {"hash": doc_hash, "chunk_ids": [c.chunk_id for c in new_chunks]}
        return len(new_chunks)

    def _remove_chunk_ids(self, chunk_ids: list[int]) -> None:
        ids = set(chunk_ids or [])
        if not ids:
            return
        for chunk in self.chunks:
            if chunk.chunk_id in ids:
                chunk.text = ""

    def search(self, query: str, top_k: int = 5, *, min_score: float | None = None) -> list[SemanticSearchResult]:
        query = (query or "").strip()
        if not query:
            return []
        with self.lock:
            idx = self._ensure_index()
            if idx.ntotal == 0 or not self.chunks:
                return []
            q_emb = self._embed([query])
            k_search = min(max(top_k * 8, top_k), idx.ntotal)
            scores, indices = idx.search(q_emb, k_search)
            threshold = self.min_score if min_score is None else min_score
            out: list[SemanticSearchResult] = []
            seen_sources: set[str] = set()
            for score, faiss_idx in zip(scores[0], indices[0]):
                if faiss_idx < 0 or faiss_idx >= len(self.chunks):
                    continue
                chunk = self.chunks[int(faiss_idx)]
                if not chunk.text or float(score) < threshold:
                    continue
                if chunk.source_uri in seen_sources and len(out) >= max(1, top_k // 2):
                    continue
                seen_sources.add(chunk.source_uri)
                out.append(SemanticSearchResult(chunk=chunk, score=float(score)))
                if len(out) >= top_k:
                    break
            return out
