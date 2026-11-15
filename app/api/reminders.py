from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter()


def _state(request: Request):
    return request.app.state


@router.get("/reminders")
def pending_reminders(state=Depends(_state)):
    return state.reminder_store.pending()


@router.get("/reminders/alerts")
def reminder_alerts(state=Depends(_state)):
    """Rappels déclenchés ou manqués pas encore affichés (l'interface les interroge)."""
    return state.reminder_store.alerts()


@router.post("/reminders/{reminder_id}/seen")
def reminder_seen(reminder_id: int, state=Depends(_state)):
    if not state.reminder_store.mark_seen(reminder_id):
        raise HTTPException(status_code=404, detail="Rappel introuvable")
    return {"id": reminder_id, "seen": True}
