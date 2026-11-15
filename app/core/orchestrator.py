from app.core.commands import NOT_STORED_NOTICE, is_personal_statement, normalize_key, parse_command
from app.core.budget import estimate_tokens, truncate_middle
from app.core.triggers import is_past_reference
from app.memory.conversations import ConversationStore
from app.memory.profile import ProfileStore

SYSTEM_PROMPT = (
    "Tu es Jarvis, un assistant personnel local. Réponds en français, de façon concise. "
    "Tu n'as actuellement aucun outil : ni agenda, ni appels, ni messages, ni domotique, "
    "ni accès à Internet. Ne prétends jamais avoir une fonctionnalité qui n'est pas "
    "décrite ici ; si on te la demande, dis simplement que tu ne peux pas encore le faire. "
    "Tu n'as aucun pouvoir sur la mémoire : tu ne peux ni enregistrer, ni modifier, ni "
    "supprimer une information. Ne dis jamais que tu as mémorisé, noté, enregistré, "
    "modifié ou oublié quelque chose. Si l'utilisateur donne une information personnelle "
    "sans commande explicite, tu peux répondre normalement, mais précise que cette "
    "information ne sera pas conservée durablement ; pour l'enregistrer, il doit écrire "
    "« Retiens que… » ou « Note que… »."
)


TRUNCATED_NOTICE = (
    "\n\n(Ton message était très long : seule une partie a été transmise au modèle, "
    "pour tenir dans son contexte.)"
)


class Orchestrator:
    def __init__(self, llm, conversations: ConversationStore, profile: ProfileStore,
                 history_limit: int = 20, episodic=None, episodic_k: int = 5,
                 num_ctx: int = 8192, reply_reserve: int = 1024, memory_budget: int = 1500):
        self.llm, self.conversations, self.profile = llm, conversations, profile
        self.history_limit = history_limit
        self.episodic, self.episodic_k = episodic, episodic_k
        self.num_ctx, self.reply_reserve, self.memory_budget = num_ctx, reply_reserve, memory_budget

    def _system_message(self) -> str:
        facts = self.profile.all()
        if not facts:
            return SYSTEM_PROMPT
        # Phrases libres (clé dérivée de la phrase, ou entrées du sprint 1) : valeur seule.
        lines = "\n".join(
            f"- {v}" if " " in k or normalize_key(v) == k else f"- {k.replace('_', ' ')} : {v}"
            for k, v in facts.items()
        )
        return (
            f"{SYSTEM_PROMPT}\n\nMémoire personnelle déjà enregistrée par l'utilisateur "
            "(fiable : utilise-la pour répondre aux questions le concernant) :\n"
            f"{lines}"
        )

    def chat(self, message: str, conversation_id: int | None = None) -> tuple[int, str]:
        if conversation_id is None or not self.conversations.exists(conversation_id):
            conversation_id = self.conversations.create()

        cmd = parse_command(message)
        if cmd:
            reply = self._run_command(cmd)
            self.conversations.add_message(conversation_id, "user", message, "command")
            self.conversations.add_message(conversation_id, "assistant", reply, "command")
            return conversation_id, reply

        # Priorité du contexte : 1) prompt système (règles + profil), jamais tronqué ;
        # 2) message courant ; 3) historique récent ; 4) souvenirs épisodiques.
        system = self._system_message()
        after_system = self.num_ctx - self.reply_reserve - estimate_tokens(system)
        prompt_message = truncate_middle(message, max(int(after_system * 0.6), 256))
        available = max(after_system - estimate_tokens(prompt_message), 0)
        past_reference = self.episodic is not None and is_past_reference(message)
        memory_reserve = min(self.memory_budget, available // 2) if past_reference else 0
        history, sent_ids = self._fit_history(conversation_id, available - memory_reserve)

        sources: list[dict] = []
        if past_reference:
            history_tokens = sum(estimate_tokens(m["content"]) for m in history)
            hits = self.episodic.search(message, self.episodic_k, exclude_message_ids=sent_ids)
            section, sources = self._episodic_section(
                hits, min(self.memory_budget, available - history_tokens))
            system += section

        messages = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": prompt_message}]
        llm_reply = self.llm.chat(messages)
        reply = llm_reply
        if prompt_message != message:
            reply += TRUNCATED_NOTICE
        if is_personal_statement(message):
            # Le LLM peut prétendre avoir mémorisé : on corrige de façon déterministe.
            reply += NOT_STORED_NOTICE
        if sources:
            reply += "\n\nSources : " + ", ".join(
                f"conversation {c} ({d})" for c, d in self._source_labels(sources))
        user_id = self.conversations.add_message(conversation_id, "user", message)
        assistant_id = self.conversations.add_message(conversation_id, "assistant", reply)
        if self.episodic is not None:
            self.episodic.index_exchange(conversation_id, user_id, assistant_id,
                                         message, llm_reply)
        return conversation_id, reply

    def _fit_history(self, conversation_id: int, budget: int) -> tuple[list[dict], list[int]]:
        """Messages les plus récents qui tiennent dans le budget (les plus anciens sont écartés)."""
        history = self.conversations.messages(
            conversation_id, self.history_limit, include_commands=False)
        window_ids = self.conversations.chat_message_ids(conversation_id, self.history_limit)
        kept: list[dict] = []
        for msg in reversed(history):
            content = truncate_middle(msg["content"], max(budget // 2, 64))
            cost = estimate_tokens(content)
            if cost > budget:
                break
            budget -= cost
            kept.append({"role": msg["role"], "content": content})
        kept.reverse()
        return kept, window_ids[len(window_ids) - len(kept):] if kept else []

    @staticmethod
    def _source_labels(sources: list[dict]) -> list[tuple[int, str]]:
        return list(dict.fromkeys((s["conversation_id"], s["created_at"][:10]) for s in sources))

    @staticmethod
    def _episodic_section(hits: list[dict], budget: int) -> tuple[str, list[dict]]:
        """Section de souvenirs qui tient dans `budget` tokens ; retourne aussi les extraits injectés."""
        if not hits:
            return (
                "\n\nL'utilisateur fait référence à une conversation passée, mais aucun souvenir "
                "pertinent n'a été retrouvé. Dis-le clairement et n'invente rien.", [])
        header = (
            "\n\nSouvenirs de conversations passées (extraits d'anciens échanges, à ne pas "
            "confondre avec la mémoire personnelle). Appuie-toi uniquement sur eux pour "
            "répondre, indique la conversation et la date d'origine, et dis si ces extraits "
            "ne permettent pas de répondre :")
        remaining = budget - estimate_tokens(header)
        blocks, used = [], []
        for hit in hits:
            block = f"[conversation {hit['conversation_id']}, {hit['created_at'][:10]}]\n{hit['display']}"
            block = truncate_middle(block, remaining) if not used and remaining >= 100 else block
            cost = estimate_tokens(block) + 2
            if cost > remaining:
                break
            remaining -= cost
            blocks.append(block)
            used.append(hit)
        if not used:
            return ("\n\nDes souvenirs existent mais n'ont pas pu être fournis faute de place dans "
                    "le contexte. Dis-le clairement et n'invente rien.", [])
        return header + "\n" + "\n\n".join(blocks), used

    def _run_command(self, cmd) -> str:
        label = cmd.key.replace("_", " ")
        if cmd.action == "recall":
            value = self.profile.get(cmd.key)
            if value is None:
                return f"Je n'ai aucune information mémorisée sur : {label}."
            return f"D'après ta mémoire personnelle, {label} : {value}."
        if cmd.action == "remember":
            previous = self.profile.get(cmd.key)
            self.profile.set(cmd.key, cmd.value)
            if previous is None:
                return f"Je note : {label} = {cmd.value}."
            return f"C'est mis à jour : {label} = {cmd.value} (avant : {previous})."
        if self.profile.delete(cmd.key):
            return f"J'ai oublié : {label}."
        return f"Je n'avais aucune information enregistrée sur : {label}."
