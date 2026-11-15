from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.commands import normalize_key
from app.core.llm import LLMError

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str


class FactRequest(BaseModel):
    key: str
    value: str


def _state(request: Request):
    return request.app.state


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, state=Depends(_state)):
    try:
        cid, reply = state.orchestrator.chat(req.message, req.conversation_id)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return ChatResponse(conversation_id=cid, reply=reply)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, state=Depends(_state)):
    if not state.conversations.exists(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return {"id": conversation_id, "messages": state.conversations.messages(conversation_id)}


@router.get("/profile")
def get_profile(state=Depends(_state)):
    return state.profile.all()


@router.get("/profile/{key}")
def get_profile_item(key: str, state=Depends(_state)):
    value = state.profile.get(normalize_key(key))
    if value is None:
        value = state.profile.get(key)  # anciennes clés (sprint 1)
    if value is None:
        raise HTTPException(status_code=404, detail="Clé introuvable")
    return {"key": normalize_key(key), "value": value}


@router.put("/profile")
def put_profile(req: FactRequest, state=Depends(_state)):
    key = normalize_key(req.key)
    if not key:
        raise HTTPException(status_code=422, detail="Clé invalide")
    state.profile.set(key, req.value)
    return state.profile.all()


@router.delete("/profile/{key}")
def delete_profile(key: str, state=Depends(_state)):
    if not (state.profile.delete(normalize_key(key)) or state.profile.delete(key)):
        raise HTTPException(status_code=404, detail="Clé introuvable")
    return state.profile.all()
