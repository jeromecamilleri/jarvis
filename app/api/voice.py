from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core.llm import LLMError
from app.voice.stt import VoiceError

router = APIRouter()
UI_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"


class SpeakRequest(BaseModel):
    text: str


def _state(request: Request):
    return request.app.state


@router.get("/ui", include_in_schema=False)
def ui():
    return FileResponse(UI_PATH)


@router.post("/voice/chat")
def voice_chat(audio: UploadFile = File(...), conversation_id: int | None = Form(None),
               state=Depends(_state)):
    """Audio -> texte -> orchestrateur habituel (mémoire comprise)."""
    try:
        transcript = state.stt.transcribe(audio.file.read())
    except VoiceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not transcript:
        return {"conversation_id": conversation_id, "transcript": "", "reply": ""}
    try:
        cid, reply = state.orchestrator.chat(transcript, conversation_id)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"conversation_id": cid, "transcript": transcript, "reply": reply}


@router.post("/voice/speak")
def voice_speak(req: SpeakRequest, state=Depends(_state)):
    try:
        wav = state.tts.synthesize(req.text)
    except VoiceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return Response(content=wav, media_type="audio/wav")
