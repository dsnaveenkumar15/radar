"""Ask Radar endpoints. Structured questions never touch Ollama; open ones run inside the shared gate."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel, Field

from core.control.spend import CappedBackend
from core.openrouter import OpenRouterBackend
from radar import ask as ask_engine
from radar import store as radar_store
from web.radar_site import radar_db, radar_only
from web.ratelimit import enforce
from web.security import client_ip

router = APIRouter(dependencies=[Depends(radar_only)])


CLIENT = httpx.Client(timeout=30)


def hosted_backend(conn, settings, inner=None):
    """Ask on OpenRouter under its own daily cap, the weekly brake and the stop-all switch; None when not configured."""
    if not (settings.ask_model and settings.openrouter_key):
        return None
    inner = inner or OpenRouterBackend(settings.ask_model, settings.openrouter_key, client=CLIENT)
    return CappedBackend(conn, inner, settings.openrouter_key, settings.ask_daily_usd, cap_setting=None)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=ask_engine.MAX_QUESTION)


@router.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request):
    from web.app import render   # local import: web.app includes this router
    return render(request, "radar_ask.html")


@router.post("/api/ask")
def api_ask(request: Request, body: AskRequest, conn=Depends(radar_db)):
    state = request.app.state
    enforce(state.ask_limiter, client_ip(request, state.settings.trust_proxy))
    question = body.question.strip()
    if len(question) < 3:
        raise HTTPException(400, "ask a question of at least 3 characters")
    registry = state.radar_registry.with_aliases(radar_store.aliases(conn))
    intent = ask_engine.route(question, state.radar_sources, registry)
    if intent:
        return ask_engine.answer_structured(conn, intent)
    backend = hosted_backend(conn, state.settings) or state.check_backend
    with state.ollama_gate.slot():
        return ask_engine.open_answer(conn, question, state.ask_embedder, backend)
