from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from invite_finder.api.deps import get_conn, get_settings
from invite_finder.api.schemas import ChatMessagesResponse, ChatRequest
from invite_finder.api.serialize import row_to_chat_message
from invite_finder.chat import ChatQueryResponse, answer_chat_query
from invite_finder.config import Settings
from invite_finder.store import chat_store, event_store, people_store

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/events/{event_id}/chat", response_model=ChatQueryResponse)
async def post_chat(
    event_id: int,
    payload: ChatRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ChatQueryResponse:
    event_row = event_store.get_by_id(conn, event_id)
    if event_row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    counts = people_store.count_event_people(conn, event_id)
    if counts["total"] == 0:
        raise HTTPException(
            status_code=409,
            detail="This event has no people yet; wait for the run to finish.",
        )

    if payload.thread_id is not None:
        thread = chat_store.get_thread(conn, payload.thread_id)
        if thread is None or thread["event_id"] != event_id:
            raise HTTPException(status_code=404, detail="Chat thread not found for this event.")

    try:
        return await answer_chat_query(
            conn,
            event_id,
            message=payload.message,
            thread_id=payload.thread_id,
            model=settings.openai_agent_model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Chat failed: {exc}") from exc


@router.get("/events/{event_id}/chat/{thread_id}", response_model=ChatMessagesResponse)
def get_chat_thread(
    event_id: int, thread_id: int, conn: sqlite3.Connection = Depends(get_conn)
) -> ChatMessagesResponse:
    thread = chat_store.get_thread(conn, thread_id)
    if thread is None or thread["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Chat thread not found for this event.")
    messages = chat_store.list_messages(conn, thread_id)
    return ChatMessagesResponse(messages=[row_to_chat_message(m) for m in messages])
