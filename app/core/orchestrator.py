import re

from app.memory.conversations import ConversationStore
from app.memory.profile import ProfileStore

SYSTEM_PROMPT = (
    "Tu es Jarvis, un assistant personnel local. Réponds en français, de façon concise. "
    "Tu n'as actuellement aucun outil : ni agenda, ni appels, ni messages, ni domotique, "
    "ni accès à Internet. Ne prétends jamais avoir une fonctionnalité qui n'est pas "
    "décrite ici ; si on te la demande, dis simplement que tu ne peux pas encore le faire."
)

# Seule une demande explicite alimente le profil : "retiens que ...", "note que ..."
REMEMBER_RE = re.compile(
    r"^\s*(?:retiens|note|souviens-toi)\s+(?:que\s+|qu')\s*(.+)$", re.I | re.S
)
FORGET_RE = re.compile(r"^\s*oublie\s+(?:que\s+|qu')?\s*(.+)$", re.I | re.S)


class Orchestrator:
    def __init__(self, llm, conversations: ConversationStore, profile: ProfileStore,
                 history_limit: int = 20):
        self.llm, self.conversations, self.profile = llm, conversations, profile
        self.history_limit = history_limit

    def _system_message(self) -> str:
        facts = self.profile.all()
        if not facts:
            return SYSTEM_PROMPT
        lines = "\n".join(f"- {v}" for v in facts.values())
        return f"{SYSTEM_PROMPT}\n\nInformations personnelles sur l'utilisateur :\n{lines}"

    def chat(self, message: str, conversation_id: int | None = None) -> tuple[int, str]:
        if conversation_id is None or not self.conversations.exists(conversation_id):
            conversation_id = self.conversations.create()

        m = REMEMBER_RE.match(message)
        if m:
            fact = m.group(1).strip()
            self.profile.set(fact.lower()[:80], fact)
            reply = "Je note cette information."
            self.conversations.add_message(conversation_id, "user", message)
            self.conversations.add_message(conversation_id, "assistant", reply)
            return conversation_id, reply

        history = self.conversations.messages(conversation_id, self.history_limit)
        messages = [{"role": "system", "content": self._system_message()}, *history,
                    {"role": "user", "content": message}]
        reply = self.llm.chat(messages)
        self.conversations.add_message(conversation_id, "user", message)
        self.conversations.add_message(conversation_id, "assistant", reply)
        return conversation_id, reply
