from app.core.commands import is_personal_statement, normalize_key, parse_command
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

NOT_STORED_NOTICE = (
    "\n\n(Rien n'a été enregistré dans ta mémoire personnelle. "
    "Pour l'enregistrer, écris « Retiens que… ».)"
)


class Orchestrator:
    def __init__(self, llm, conversations: ConversationStore, profile: ProfileStore,
                 history_limit: int = 20):
        self.llm, self.conversations, self.profile = llm, conversations, profile
        self.history_limit = history_limit

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

        # Les commandes de mémoire restent dans SQLite mais ne sont pas montrées au LLM.
        history = self.conversations.messages(
            conversation_id, self.history_limit, include_commands=False)
        messages = [{"role": "system", "content": self._system_message()}, *history,
                    {"role": "user", "content": message}]
        reply = self.llm.chat(messages)
        if is_personal_statement(message):
            # Le LLM peut prétendre avoir mémorisé : on corrige de façon déterministe.
            reply += NOT_STORED_NOTICE
        self.conversations.add_message(conversation_id, "user", message)
        self.conversations.add_message(conversation_id, "assistant", reply)
        return conversation_id, reply

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
