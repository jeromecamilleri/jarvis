"""Mémoire épisodique : extraits de conversations passées (FTS5 + embeddings locaux)."""
import re
import sqlite3
import unicodedata

import numpy as np

from app.core.commands import MEMORY_CLAIM_CORRECTION, NOT_STORED_NOTICE, REMINDER_CLAIM_CORRECTION
from app.core.llm import LLMError
from app.core.triggers import is_past_reference

CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
MAX_PARTS = 100  # par message : un énorme collage n'est indexé que sur ses ~80 000 premiers caractères
# Seuils mesurés avec bge-m3 : extraits pertinents 0,58–0,71 ; bruit jusqu'à ~0,57.
VECTOR_MIN_COSINE = 0.55  # candidat uniquement sémantique
FTS_MIN_COSINE = 0.45  # candidat FTS5 (déjà une preuve lexicale) : contrôle de pertinence
CANDIDATES = 20
RRF_K = 60
EMBED_BATCH = 32

STOPWORDS = {
    "avons", "avions", "avez", "avait", "avaient", "nous", "vous", "dans", "pour", "avec",
    "quel", "quelle", "quels", "quelles", "que", "qui", "quoi", "les", "des", "une", "est",
    "sont", "mon", "mes", "ton", "tes", "notre", "nos", "sur", "par", "pas", "plus", "etait",
    "decide", "decides", "dit", "parle", "choisi", "semaine", "derniere", "passee", "mois",
    "dernier", "passe", "hier", "jour", "autre", "souviens", "rappelle", "concernant",
    "tout", "tous", "cette", "cet", "ces", "aux", "elle", "ils", "lui", "moi", "toi",
    "sais", "peux", "veux", "fait", "faire", "etre", "avoir",
    "utilisateur", "jarvis",
}


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Découpe en fenêtres d'environ `size` caractères, de préférence sur une fin de phrase."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    pieces, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            floor = start + int(size * 0.6)
            for sep in ("\n\n", "\n", ". ", " "):
                cut = text.rfind(sep, floor, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in text if not unicodedata.combining(c))


def fts_query(text: str) -> str | None:
    tokens = [t for t in re.findall(r"[a-z0-9]+", _fold(text))
              if len(t) >= 3 and t not in STOPWORDS]
    terms = []
    for t in dict.fromkeys(tokens):
        # Préfixe pour tolérer les variations de conjugaison (« décidé » / « décider »).
        terms.append(f'"{t[:-2]}"*' if len(t) >= 7 else f'"{t}"')
    return " OR ".join(terms) or None


class EpisodicMemory:
    def __init__(self, conn: sqlite3.Connection, embedder=None):
        self.conn, self.embedder = conn, embedder

    # --- indexation
    def index_exchange(self, conversation_id: int, user_id: int, assistant_id: int,
                       user_text: str, reply_text: str) -> None:
        if is_past_reference(user_text) and "?" in user_text:
            return  # une question de rappel n'est pas un souvenir
        if self.conn.execute(
                "SELECT 1 FROM chunks WHERE first_message_id = ?", (user_id,)).fetchone():
            return
        created = self.conn.execute(
            "SELECT created_at FROM messages WHERE id = ?", (user_id,)).fetchone()["created_at"]
        pieces = self._pieces(user_text, reply_text)
        try:
            for part, (text, display) in enumerate(pieces):
                cur = self.conn.execute(
                    "INSERT INTO chunks (conversation_id, first_message_id, "
                    "last_message_id, part, created_at, text, display) VALUES (?,?,?,?,?,?,?)",
                    (conversation_id, user_id, assistant_id, part, created, text, display))
                self.conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                                  (cur.lastrowid, text))
            self.conn.commit()  # tous les extraits d'un échange ou aucun
        except sqlite3.IntegrityError:
            self.conn.rollback()
            if self.conn.execute("SELECT 1 FROM chunks WHERE first_message_id = ?",
                                 (user_id,)).fetchone():
                return  # indexé entre-temps par un autre fil : rien à faire
            raise
        except sqlite3.Error:
            self.conn.rollback()
            raise
        self.embed_pending()

    @staticmethod
    def _pieces(user_text: str, reply_text: str) -> list[tuple[str, str]]:
        """(texte indexé sans étiquettes, texte affiché avec étiquettes)."""
        user_text, reply_text = user_text.strip(), reply_text.strip()
        if len(user_text) + len(reply_text) <= CHUNK_CHARS:
            return [(f"{user_text}\n{reply_text}",
                     f"Utilisateur : {user_text}\nJarvis : {reply_text}")]
        pieces = [(p, f"Utilisateur : {p}") for p in split_text(user_text)[:MAX_PARTS]]
        pieces += [(p, f"Jarvis : {p}") for p in split_text(reply_text)[:MAX_PARTS]]
        return pieces

    def backfill(self) -> int:
        """Indexe les anciens échanges conversationnels pas encore indexés."""
        rows = self.conn.execute(
            "SELECT id, conversation_id, role, content FROM messages "
            "WHERE kind != 'command' ORDER BY conversation_id, id").fetchall()
        count = "SELECT COUNT(DISTINCT first_message_id) FROM chunks"
        before = self.conn.execute(count).fetchone()[0]
        for cur, nxt in zip(rows, rows[1:]):
            if (cur["role"] == "user" and nxt["role"] == "assistant"
                    and cur["conversation_id"] == nxt["conversation_id"]):
                self._index_no_embed(cur, nxt)
        return self.conn.execute(count).fetchone()[0] - before

    def _index_no_embed(self, user, assistant) -> None:
        embedder, self.embedder = self.embedder, None
        try:
            reply = assistant["content"].replace(NOT_STORED_NOTICE, "")
            reply = reply.replace(MEMORY_CLAIM_CORRECTION, "").replace(REMINDER_CLAIM_CORRECTION, "")
            self.index_exchange(user["conversation_id"], user["id"], assistant["id"],
                                user["content"], reply)
        finally:
            self.embedder = embedder

    def embed_pending(self, limit: int = 64) -> None:
        if not self.embedder:
            return
        rows = self.conn.execute(
            "SELECT id, text FROM chunks WHERE embedding IS NULL ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        for i in range(0, len(rows), EMBED_BATCH):
            batch = rows[i:i + EMBED_BATCH]
            try:
                vectors = self.embedder.embed([r["text"] for r in batch])
            except LLMError:
                return  # Ollama indisponible : on réessaiera plus tard
            for r, v in zip(batch, vectors):
                self.conn.execute("UPDATE chunks SET embedding = ? WHERE id = ?",
                                  (self._to_blob(v), r["id"]))
            self.conn.commit()

    @staticmethod
    def _to_blob(vector) -> bytes:
        v = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(v)
        return (v / norm if norm else v).tobytes()

    # --- recherche
    def _fts_ranked(self, query: str) -> list[int]:
        q = fts_query(query)
        if not q:
            return []
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?", (q, CANDIDATES)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    def _query_vector(self, query: str):
        if not self.embedder:
            return None
        try:
            return np.frombuffer(self._to_blob(self.embedder.embed([query])[0]), dtype=np.float32)
        except LLMError:
            return None

    def _vector_ranked(self, qvec) -> list[int]:
        if qvec is None:
            return []
        rows = self.conn.execute(
            "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
        if not rows:
            return []
        matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        sims = matrix @ qvec
        order = np.argsort(-sims)[:CANDIDATES]
        return [rows[i]["id"] for i in order if sims[i] >= VECTOR_MIN_COSINE]

    def _relevant_fts(self, chunk_ids: list[int], qvec) -> list[int]:
        """Contrôle de pertinence des candidats FTS5 par embeddings (sans règle de mots communs).

        Sans embedding disponible (Ollama arrêté, extrait pas encore vectorisé), le candidat est gardé.
        """
        if qvec is None or not chunk_ids:
            return chunk_ids
        marks = ",".join("?" * len(chunk_ids))
        blobs = {r["id"]: r["embedding"] for r in self.conn.execute(
            f"SELECT id, embedding FROM chunks WHERE id IN ({marks})", chunk_ids)}
        keep = []
        for cid in chunk_ids:
            blob = blobs.get(cid)
            if blob is None or float(np.frombuffer(blob, dtype=np.float32) @ qvec) >= FTS_MIN_COSINE:
                keep.append(cid)
        return keep

    def search(self, query: str, k: int = 5, exclude_message_ids=()) -> list[dict]:
        self.embed_pending()
        excluded = set(exclude_message_ids)
        qvec = self._query_vector(query)
        fts = self._relevant_fts(self._fts_ranked(query), qvec)
        scores: dict[int, float] = {}
        for ranked in (fts, self._vector_ranked(qvec)):
            for rank, chunk_id in enumerate(ranked):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        results = []
        for chunk_id in sorted(scores, key=scores.get, reverse=True):
            row = self.conn.execute(
                "SELECT id, conversation_id, first_message_id, part, created_at, text, display "
                "FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row and row["first_message_id"] not in excluded:
                results.append({**dict(row), "score": scores[chunk_id]})
            if len(results) == k:
                break
        return results
