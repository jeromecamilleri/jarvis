#!/usr/bin/env python3
"""Faux serveur MCP Kensaku pour les tests (stdio, JSON-RPC une ligne par message).

Comportement choisi par la variable d'environnement FAKE_MODE :
  ok            réponses normales
  die_once      meurt à la 1re requête tools/call (si FAKE_MARK n'existe pas encore), puis normal
  die_always    meurt à chaque tools/call
  hang          ne répond jamais à tools/call
  garbage       répond du texte non JSON
  rpc_error     répond une erreur JSON-RPC à tools/call
  is_error      répond isError=true
  empty_text    content[0].text vide
  bad_json      content[0].text non JSON
  no_results    résultats vides
  many          renvoie 20 résultats aux extraits très longs
  big_snippet   read_snippet renvoie un texte énorme
  rank          résultats nommés : tests/test_functions.py, app/core/function_registry.py, docs/notes.md, ...
  empty_read    read_snippet renvoie un texte vide
  indexing      index_state renvoie is_indexing=true
  reindex_refused  reindex répond started=false
  reindex_bad   reindex répond un JSON sans `started`
  state_error   index_state échoue (erreur JSON-RPC) après la poignée de main
  state_partial index_state sans les champs extract_total et paused
  indexing_str  index_state avec is_indexing="false" (chaîne)
  indexing_missing  index_state sans is_indexing
  noisy         émet des notifications et une réponse tardive avant la bonne réponse
Le serveur écrit les outils appelés dans FAKE_LOG (un nom par ligne) s'il est défini.
"""
import json
import os
import sys

MODE = os.environ.get("FAKE_MODE", "ok")
LOG = os.environ.get("FAKE_LOG")
MARK = os.environ.get("FAKE_MARK")
ROOT = "/data/docs"
STATES = [0]


def reply(i, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": i}
    msg["error" if error else "result"] = error or result
    print(json.dumps(msg), flush=True)


def text(payload, is_error=False):
    return {"content": [{"type": "text", "text": payload if isinstance(payload, str) else json.dumps(payload)}],
            "isError": is_error}


for line in sys.stdin:
    req = json.loads(line)
    method, i = req.get("method"), req.get("id")
    if method == "initialize":
        reply(i, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "0"}})
    elif method == "notifications/initialized":
        if LOG:
            open(LOG, "a").write("initialized\n")
    elif method == "tools/call":
        name, args = req["params"]["name"], req["params"]["arguments"]
        if LOG:
            open(LOG, "a").write(f"{name} {json.dumps(args)}\n")
        if name == "index_state":
            STATES[0] += 1
            if MODE == "state_error" and STATES[0] > 1:
                reply(i, error={"code": -32000, "message": "state unavailable"})
                continue
            state = {"roots": [ROOT], "phase": "idle", "is_indexing": MODE == "indexing", "files": 3,
                     "files_indexed": 3, "content_docs": 3, "content_indexed": 2, "extract_done": 1,
                     "extract_total": 3, "semantic_units": 9, "index_bytes": 1234, "as_of": "2026-11-15T22:00:00",
                     "paused": False, "secret_internal": "x"}
            if MODE == "state_partial":
                del state["extract_total"], state["paused"]
            elif MODE == "indexing_str":
                state["is_indexing"] = "false"
            elif MODE == "indexing_missing":
                del state["is_indexing"]
            reply(i, text(state))
            continue
        if MODE == "die_always" or (MODE == "die_once" and MARK and not os.path.exists(MARK)):
            if MARK:
                open(MARK, "w").write("x")
            sys.exit(1)
        if MODE == "hang":
            continue
        if MODE == "garbage":
            print("ceci n'est pas du json", flush=True)
            continue
        if MODE == "rpc_error":
            reply(i, error={"code": -32602, "message": "Invalid params: path is not part of the index"})
        elif MODE == "is_error":
            reply(i, text("boom interne", is_error=True))
        elif MODE == "empty_text":
            reply(i, text(""))
        elif MODE == "bad_json":
            reply(i, text("{pas du json"))
        elif name == "search":
            n = {"no_results": 0, "many": 20}.get(MODE, 2)
            results = [{"path": f"{ROOT}/f{k}.md", "score": 1.5 - k / 100, "match_type": "Semantic",
                        "snippet": ("x" * 1000) if MODE == "many" else f"extrait {k} pour {args['query']}"}
                       for k in range(n)]
            if MODE == "rank":
                results = [{"path": f"{ROOT}/{p}", "score": sc, "match_type": mt, "snippet": sn}
                           for p, sc, mt, sn in [
                               ("tests/test_functions.py", 1.0, "Semantic", ""),
                               ("app/core/function_registry.py", 0.8, "Semantic", ""),
                               ("docs/notes.md", 0.7, "Semantic", ""),
                               ("docs/function_registry_old.md", 0.05, "Semantic", ""),
                               ("README.md", 0.6, "Content", "registre")]]
            if MODE == "noisy":
                print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}), flush=True)
                reply(i - 50, text({"results": []}))  # réponse tardive d'un échange ancien
            reply(i, text({"results": results, "has_more": n > 5}))
        elif name == "reindex":
            reply(i, text({"started": False} if MODE == "reindex_refused" else
                           {"ok": True} if MODE == "reindex_bad" else {"started": True}))
        elif name == "read_snippet":
            body = ("y" * 20000) if MODE == "big_snippet" else "" if MODE == "empty_read" else f"passage de {args['path']}"
            reply(i, text({"path": args["path"], "line_start": 3, "line_end": 4, "text": body, "truncated": False}))
        else:
            reply(i, error={"code": -32602, "message": f"Unknown tool: {name}"})
    elif i is not None:
        reply(i, error={"code": -32601, "message": "Method not found"})
