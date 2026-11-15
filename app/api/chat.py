from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

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


@router.put("/profile")
def put_profile(req: FactRequest, state=Depends(_state)):
    state.profile.set(req.key, req.value)
    return state.profile.all()


@router.delete("/profile/{key}")
def delete_profile(key: str, state=Depends(_state)):
    if not state.profile.delete(key):
        raise HTTPException(status_code=404, detail="Clé introuvable")
    return state.profile.all()
