"""Recherche documentaire locale via le serveur MCP de Kensaku (`kensaku-cli mcp`, stdio, lecture seule).

Jarvis lance lui-même le processus (pas de service systemd, pas de port réseau), garde une session
persistante et la ferme à l'arrêt. Protocole : JSON-RPC 2.0, un message JSON par ligne ;
poignée de main `initialize` puis `notifications/initialized`.

Seuls `search`, `read_snippet` (et `index_state` en interne, pour connaître les racines indexées) sont
appelés. Jamais de réindexation. L'index peut être périmé si l'application Kensaku est fermée.

Vérification manuelle :  python -m app.functions.kensaku check "architecture générale de Jarvis"
"""
import json
import logging
import os
import queue
import subprocess
import sys
import threading

from app.core.function_registry import READ, FunctionError, FunctionRegistry, FunctionSpec, Param

log = logging.getLogger("jarvis.kensaku")

PROTOCOL_VERSION = "2024-11-05"
MAX_RESULTS, MIN_RESULTS, DEFAULT_RESULTS = 5, 3, 5
FETCH_RESULTS = 10  # demandés à Kensaku, puis reclassés et réduits à MAX_RESULTS
STOPWORDS = {"le", "la", "les", "un", "une", "de", "du", "des", "et", "ou", "en", "dans", "sur", "fichier",
             "document", "module", "code", "lis", "lire", "explique", "moi", "ses", "son", "sa", "the", "of"}
WEAK_SCORE = 0.25  # un nom de fichier ne remonte pas un résultat dont le score est < 25 % du meilleur
MAX_SNIPPET_CHARS = 200
MAX_READ_TOKENS, MIN_READ_TOKENS = 512, 32
MAX_KNOWN_PATHS = 500
UNAVAILABLE = "La recherche documentaire (Kensaku) est indisponible"


class KensakuClient:
    """Session MCP persistante. Les échanges sont sérialisés par un verrou (pas de réponses croisées)."""

    def __init__(self, command: list[str], timeout: float = 15.0):
        self.command, self.timeout = command, timeout
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0
        self.roots: list[str] = []
        self.known_paths: dict[str, None] = {}  # chemins issus de docs_search (ordre d'insertion)

    # --- cycle de vie --------------------------------------------------------------------
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Lance le processus et fait la poignée de main. Lève FunctionError en cas d'échec."""
        with self._lock:
            self._stop_locked()
            try:
                self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
            except OSError as e:
                self._proc = None
                raise FunctionError(f"{UNAVAILABLE} : programme introuvable ou non exécutable "
                                    f"({self.command[0]}).") from e
            self._lines = queue.Queue()
            threading.Thread(target=self._reader, args=(self._proc, self._lines), daemon=True).start()
            try:
                self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                         "clientInfo": {"name": "jarvis", "version": "1"}})
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                state = self._call_tool("index_state", {})
                roots = state.get("roots") if isinstance(state, dict) else None
                self.roots = [os.path.normpath(r) for r in roots if isinstance(r, str)] if roots else []
            except Exception:
                self._stop_locked()
                raise
        log.info("Kensaku démarré (racines : %s)", self.roots)

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()  # EOF sur stdin : arrêt normal du serveur MCP
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdout:
            proc.stdout.close()

    @staticmethod
    def _reader(proc, lines: queue.Queue) -> None:
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)  # fin de flux : processus mort

    # --- JSON-RPC ------------------------------------------------------------------------
    def _send(self, message: dict) -> None:
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            raise _Dead() from e

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        wanted = self._next_id
        self._send({"jsonrpc": "2.0", "id": wanted, "method": method, "params": params})
        while True:
            try:
                line = self._lines.get(timeout=self.timeout)
            except queue.Empty:
                self._stop_locked()  # flux désynchronisé : on repart d'une session propre
                raise FunctionError(f"{UNAVAILABLE} : délai de {self.timeout:g} s dépassé.") from None
            if line is None:
                raise _Dead()
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                raise FunctionError(f"{UNAVAILABLE} : réponse illisible du serveur MCP.") from None
            if not isinstance(message, dict):
                raise FunctionError(f"{UNAVAILABLE} : réponse MCP inattendue.")
            if message.get("id") != wanted:
                continue  # notification ou réponse tardive d'un échange précédent
            if "error" in message:
                err = message["error"] if isinstance(message["error"], dict) else {}
                raise FunctionError(f"Kensaku a refusé la requête : {str(err.get('message', 'erreur inconnue'))[:200]}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise FunctionError(f"{UNAVAILABLE} : réponse MCP sans résultat.")
            return result

    def _call_tool(self, name: str, arguments: dict):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content")
        text = content[0].get("text") if isinstance(content, list) and content and isinstance(content[0], dict) else None
        if result.get("isError"):
            raise FunctionError(f"Kensaku a signalé une erreur : {(text or 'inconnue')[:200]}")
        if not isinstance(text, str) or not text.strip():
            raise FunctionError("Kensaku a renvoyé une réponse vide.")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise FunctionError(f"{UNAVAILABLE} : réponse mal formée.") from None

    def call_tool(self, name: str, arguments: dict):
        """Appelle un outil MCP. Processus mort : une seule tentative de redémarrage, puis erreur claire."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if not self.alive():
                        self.start()
                    return self._call_tool(name, arguments)
                except _Dead:
                    self._stop_locked()
                    if attempt == 2:
                        raise FunctionError(f"{UNAVAILABLE} : le processus s'est arrêté.") from None
                    log.warning("Kensaku s'est arrêté : redémarrage")


def _normalize(text: str) -> str:
    text = text.lower()
    for ch in "_-./\\":
        text = text.replace(ch, " ")
    return " ".join(text.split())


class _Dead(Exception):
    """Le processus MCP est mort (EOF ou écriture impossible)."""


class Kensaku:
    """Fonctions `docs_search` et `docs_read` exposées au LLM."""

    def __init__(self, client: KensakuClient):
        self.client = client

    def docs_search(self, query: str, max_results: int = DEFAULT_RESULTS) -> dict:
        if not query.strip():
            raise FunctionError("La requête de recherche est vide.")
        max_results = max(MIN_RESULTS, min(max_results, MAX_RESULTS))
        data = self.client.call_tool("search", {"query": query.strip(), "page_size": FETCH_RESULTS})
        raw = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise FunctionError(f"{UNAVAILABLE} : réponse de recherche mal formée.")
        results = []
        for item in raw[:FETCH_RESULTS]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            try:
                score = round(float(item.get("score") or 0), 3)
            except (TypeError, ValueError):
                score = 0.0
            snippet = str(item.get("snippet") or "")
            results.append({"path": item["path"], "score": score, "match_type": item.get("match_type"),
                            "snippet": snippet[:MAX_SNIPPET_CHARS]})
        results = self._rerank(query, results)[:max_results]
        for r in results:
            self._remember(r["path"])
        if not results:
            return {"count": 0, "results": [], "note": "Aucun document indexé ne correspond à cette recherche."}
        return {"count": len(results), "results": results,
                "note": ("Ces résultats ne prouvent pas le contenu des fichiers. Pour répondre sur un fichier, "
                         "choisis celui dont le nom correspond à la demande et lis-le avec docs_read.")}

    def _rerank(self, query: str, results: list[dict]) -> list[dict]:
        """Priorité aux noms de fichiers/chemins qui correspondent à la requête, sauf contenu hors sujet.
        Sans correspondance de nom, l'ordre de Kensaku est conservé (tri stable)."""
        q = _normalize(query)
        words = [w for w in q.split() if w not in STOPWORDS and len(w) > 1]
        best = max((r["score"] for r in results), default=0)
        for r in results:
            rel = _normalize(self._relative(r["path"]))
            name = _normalize(os.path.basename(r["path"]))
            exact = bool(name) and f" {name} " in f" {q} " and bool(os.path.splitext(r["path"])[1])
            if exact or (words and all(w in name.split() or w in name for w in words)):
                tier = "file"
            elif words and any(w in rel for w in words):
                tier = "path"
            else:
                tier = None
            if tier and not exact and r["score"] < WEAK_SCORE * best and not r["snippet"]:
                tier = None  # nom correspondant mais contenu manifestement hors sujet
            r["name_match"] = tier
        order = {"file": 0, "path": 1, None: 2}
        return sorted(results, key=lambda r: order[r["name_match"]])

    def _relative(self, path: str) -> str:
        for root in self.client.roots:
            if path.startswith(root.rstrip(os.sep) + os.sep):
                return path[len(root.rstrip(os.sep)) + 1:]
        return path

    def docs_read(self, path: str, query: str = "", max_tokens: int = MAX_READ_TOKENS) -> dict:
        self._check_path(path)
        arguments = {"path": path, "max_tokens": max(MIN_READ_TOKENS, min(max_tokens, MAX_READ_TOKENS))}
        if query.strip():
            arguments["query"] = query.strip()
        data = self.client.call_tool("read_snippet", arguments)
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise FunctionError(f"{UNAVAILABLE} : extrait mal formé.")
        if not text.strip():
            return {"path": path, "text": "", "note": "Aucun contenu lisible dans ce fichier."}
        limit = arguments["max_tokens"] * 4  # ≈ 4 caractères par token
        out = {"path": path, "line_start": data.get("line_start"), "line_end": data.get("line_end"),
               "text": text[:limit], "truncated": bool(data.get("truncated")) or len(text) > limit}
        if "query" in arguments:
            # Correspondance littérale du texte demandé dans ce passage, pas une mesure de pertinence.
            found = arguments["query"].lower() in text[:limit].lower()
            out["query_found"] = found
            out["note"] = ("Le texte de `query` apparaît littéralement dans ce passage." if found else
                           "Le texte de `query` est ABSENT de ce passage (début du fichier) : n'en tire aucune "
                           "conclusion sur ce point ; relis avec une autre `query` (mot exact du fichier).")
        return out

    # --- contrôles ---------------------------------------------------------------------
    def _remember(self, path: str) -> None:
        known = self.client.known_paths
        known.pop(path, None)
        known[path] = None
        while len(known) > MAX_KNOWN_PATHS:
            del known[next(iter(known))]

    def _check_path(self, path: str) -> None:
        if not os.path.isabs(path) or ".." in path.split(os.sep) or os.path.normpath(path) != path:
            raise FunctionError("Le chemin doit être absolu, sans « .. », tel que renvoyé par docs_search.")
        roots = self.client.roots
        if not roots:  # racines connues à la poignée de main ; sinon le service n'a jamais démarré
            self.client.call_tool("index_state", {})
            roots = self.client.roots
        if not any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            raise FunctionError("Ce fichier n'est pas dans les dossiers indexés par Kensaku.")
        if path not in self.client.known_paths:
            raise FunctionError("Ce chemin ne provient pas d'un résultat de docs_search : lance d'abord docs_search.")


def register(registry: FunctionRegistry, settings, client: KensakuClient | None = None) -> KensakuClient | None:
    """Enregistre docs_search / docs_read si Kensaku est activé. Ne lance pas le processus (cf. lifespan)."""
    if not settings.kensaku_enabled:
        return None
    client = client or KensakuClient([os.path.expanduser(settings.kensaku_cli_path), "mcp"],
                                     settings.kensaku_timeout)
    kensaku = Kensaku(client)
    registry.register(FunctionSpec(
        name="docs_search",
        description=("Recherche dans les fichiers locaux indexés (documentation et code du projet Jarvis). Retourne "
                     "chemins, scores et courts extraits, qui NE SUFFISENT PAS pour décrire un fichier. Pour "
                     "expliquer ou citer le contenu d'un fichier : docs_search, puis docs_read sur le bon fichier, "
                     "et réponds UNIQUEMENT d'après le texte lu, sans rien inventer ni compléter avec tes "
                     "connaissances générales. Si le fichier est introuvable ou illisible, dis-le et demande "
                     "une précision. Ne concerne ni la mémoire personnelle ni l'agenda."),
        params=(Param("query", "string", "Nom du fichier, mots-clés ou question."),
                Param("max_results", "integer", f"Nombre de résultats (entre {MIN_RESULTS} et {MAX_RESULTS}).",
                      required=False, minimum=1, maximum=MAX_RESULTS)),
        handler=kensaku.docs_search, level=READ, document="search", log_args=("query", "max_results")))
    registry.register(FunctionSpec(
        name="docs_read",
        description=("Lit un passage d'un fichier trouvé par docs_search (chemin exact d'un résultat). Le passage est "
                     "centré sur la première occurrence littérale de `query`, sinon c'est le début du fichier. "
                     "`query_found` indique seulement si ce texte exact figure dans le passage, pas si le passage "
                     "répond à la question. Réponds uniquement d'après le texte lu."),
        params=(Param("path", "string", "Chemin absolu exact renvoyé par docs_search."),
                Param("query", "string", "Mot ou nom EXACT présent dans le fichier (nom de constante, mot du code), "
                      "jamais une traduction.", required=False),
                Param("max_tokens", "integer", f"Taille du passage en tokens (défaut et maximum {MAX_READ_TOKENS}).",
                      required=False, minimum=MIN_READ_TOKENS, maximum=MAX_READ_TOKENS)),
        handler=kensaku.docs_read, level=READ, document="read",
        log_args=("path", "query", "max_tokens")))
    return client


def _cli(argv: list[str]) -> int:
    from app.config import get_settings
    settings = get_settings()
    if len(argv) < 2 or argv[0] != "check":
        print('Usage : python -m app.functions.kensaku check "<question>"')
        return 2
    client = KensakuClient([os.path.expanduser(settings.kensaku_cli_path), "mcp"], settings.kensaku_timeout)
    kensaku = Kensaku(client)
    try:
        client.start()
        print(f"Session MCP ouverte (racines : {client.roots})")
        found = kensaku.docs_search(" ".join(argv[1:]))
        print(json.dumps(found, ensure_ascii=False, indent=2))
        if found["results"]:
            first = found["results"][0]["path"]
            print(f"\n--- docs_read {first} ---")
            print(json.dumps(kensaku.docs_read(first, " ".join(argv[1:]), 128), ensure_ascii=False, indent=2))
        return 0
    except FunctionError as e:
        print(f"Échec : {e}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
