"""前端只连接自有 /api/agent/ws；百炼凭证和业务权限留在后端。"""
import asyncio
from contextlib import suppress
import logging
from .telemetry import Trace, current_trace, redacted, business
from .service import SYSTEM_PROMPT, TOOLS
from typing import Annotated, Literal
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.websockets import WebSocketState

from app.db.session import SessionLocal
from app.features.auth.models import UserModel
from app.features.auth.router import COOKIE_NAME, require_current_user
from .provider import AgentError, BailianProvider
from .service import AgentEngine, AgentStore, CONFIRM_WORDS, DECLINE_WORDS, normalized
from .settings import AgentSettings

router = APIRouter(prefix="/agent", tags=["agent"])
store = AgentStore(SessionLocal)
logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 16000 * 2 * 60


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestEvent(Event):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatSend(RequestEvent):
    type: Literal["chat.send"]
    text: str = Field(min_length=1, max_length=2000)
    speak: bool = True


class AudioStart(RequestEvent):
    type: Literal["audio.start"]
    format: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    speak: bool = True


class AudioCommit(RequestEvent):
    type: Literal["audio.commit"]


class TurnCancel(RequestEvent):
    type: Literal["turn.cancel"]


class ActionConfirm(RequestEvent):
    type: Literal["action.confirm"]
    action_id: UUID
    speak: bool = True


class ActionDecline(RequestEvent):
    type: Literal["action.decline"]
    speak: bool = False


class HandoffRequest(RequestEvent):
    type: Literal["handoff.request"]
    summary: str = Field(min_length=1, max_length=1000)
    speak: bool = False


class Ping(Event):
    type: Literal["ping"]


CLIENT_EVENT = TypeAdapter(Annotated[
    ChatSend | AudioStart | AudioCommit | TurnCancel | ActionConfirm | ActionDecline | HandoffRequest | Ping,
    Field(discriminator="type"),
])


class TicketUpdate(Event):
    state: Literal["in_progress", "resolved"]


def http_error(error):
    return HTTPException(401 if error.code == "unauthorized" else 404 if error.code == "not_found" else 409,
                         detail=str(error))


@router.get("/status")
def status(actor: UserModel = Depends(require_current_user)):
    return {"capabilities": AgentSettings.from_env().capabilities(),
            "limits": {"audio_seconds": 60, "text_characters": 2000, "confirmation_seconds": 300},
            "audio_input": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "audio_output": {"format": "pcm_s16le", "sample_rate": 24000, "channels": 1}}


@router.post("/conversations", status_code=201)
def create_conversation(actor: UserModel = Depends(require_current_user)):
    return store.create_conversation(actor.id)


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, actor: UserModel = Depends(require_current_user)):
    try:
        return store.conversation(str(conversation_id), actor.id)
    except AgentError as error:
        raise http_error(error) from error


@router.get("/tickets")
def tickets(page: int = Query(1, ge=1), actor: UserModel = Depends(require_current_user)):
    return {"items": store.tickets(actor.id, page), "page": page, "page_size": 20}


@router.delete("/conversations/{conversation_id}", status_code=204)
def archive_conversation(conversation_id: UUID, actor: UserModel = Depends(require_current_user)):
    try:
        store.archive_conversation(str(conversation_id), actor.id)
        return Response(status_code=204)
    except AgentError as error:
        raise http_error(error) from error


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: UUID, actor: UserModel = Depends(require_current_user)):
    try:
        return store.ticket_detail(actor.id, str(ticket_id))
    except AgentError as error:
        raise http_error(error) from error


@router.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: UUID, payload: TicketUpdate, actor: UserModel = Depends(require_current_user)):
    try:
        return store.ticket_detail(actor.id, str(ticket_id), payload.state)
    except AgentError as error:
        raise http_error(error) from error


class AgentSocket:
    def __init__(self, socket, conversation_id, token, actor, provider):
        self.socket, self.conversation_id, self.token, self.actor = socket, conversation_id, token, actor
        self.provider = provider
        self.engine = AgentEngine(store, provider)
        self.task = None
        self.request_id = None
        self.queue = None
        self.audio_bytes = 0
        self.audio_committed = False
        self.committing = False
        self.speaking = False
        self.closed = False

    async def emit(self, event_type, request_id=None, **body):
        if not self.closed:
            try:
                await self.socket.send_json({"type": event_type, **({"request_id": request_id} if request_id else {}), **body})
            except (WebSocketDisconnect, RuntimeError, OSError):
                self.closed = True

    async def error(self, error, request_id=None):
        await self.emit("error", request_id, code=error.code, message=str(error))

    async def cancel_active(self):
        task, request_id = self.task, self.request_id
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # task可能在协程首次执行前被取消，此时process里的finally尚未建立。
        if request_id and self.request_id == request_id:
            await asyncio.to_thread(store.release, self.conversation_id, self.actor["id"], request_id, True)
            self.queue, self.task, self.request_id = None, None, None
            await self.emit("turn.done", request_id, status="cancelled")

    async def run(self):
        await self.emit("session.ready", conversation_id=self.conversation_id, protocol_version=1,
                        capabilities=self.provider.settings.capabilities())
        try:
            while True:
                packet = await asyncio.wait_for(self.socket.receive(), timeout=120)
                if packet["type"] == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    await self.audio_chunk(packet["bytes"])
                    continue
                raw = packet.get("text", "")
                if len(raw) > 12000:
                    await self.error(AgentError("invalid_event", "消息过长。"))
                    continue
                try:
                    event = CLIENT_EVENT.validate_json(raw)
                except ValidationError:
                    await self.error(AgentError("invalid_event", "消息格式不正确，请检查事件类型和参数。"))
                    continue
                try:
                    # 长连接建立后退出登录/会话过期，下一条控制消息立即拒绝。
                    await asyncio.to_thread(store.identity, self.token)
                    await self.dispatch(event)
                except AgentError as error:
                    await self.error(error, getattr(event, "request_id", None))
                    if error.code == "unauthorized":
                        await self.socket.close(code=1008)
                        break
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            pass
        finally:
            self.closed = True
            with CancelScope(shield=True):
                if self.task:
                    # 已确认的业务写入需要完成并保留结果；断线不能撤销已执行的订单操作。
                    with suppress(asyncio.CancelledError, Exception):
                        if self.committing:
                            await self.task
                        else:
                            await self.cancel_active()
                if self.socket.application_state == WebSocketState.CONNECTED:
                    with suppress(Exception):
                        await self.socket.close(code=1000)

    async def dispatch(self, event):
        if isinstance(event, Ping):
            await self.emit("pong")
            return
        if isinstance(event, TurnCancel):
            if self.request_id != event.request_id or not self.task:
                raise AgentError("no_active_turn", "该请求当前未在处理。")
            if self.committing:
                raise AgentError("action_in_progress", "已确认的操作正在执行，请等待结果。")
            await self.cancel_active()
            return
        if isinstance(event, AudioCommit):
            if not self.queue or self.request_id != event.request_id or self.audio_committed:
                raise AgentError("audio_state", "当前没有可提交的录音。")
            if self.audio_bytes < 9600:
                await self.cancel_active()
                raise AgentError("audio_too_short", "说话时间太短，请重新按住说话。")
            if self.queue.full():
                raise AgentError("audio_backpressure", "语音上传繁忙，请稍后提交。")
            self.audio_committed = True
            self.queue.put_nowait(None)
            await self.emit("audio.committed", event.request_id)
            return
        if self.speaking and self.task and not self.task.done():
            # 用户按住说话、点击确认或发送文字时，先中断上一轮播报，保留已发布的确认卡片。
            await self.cancel_active()
        if self.task and not self.task.done():
            raise AgentError("busy", "上一条请求正在处理，请先结束或取消。")
        if isinstance(event, ChatSend) and not event.text.strip():
            raise AgentError("empty_text", "请输入问题。")
        if isinstance(event, (AudioStart, ChatSend)):
            self.provider.require_key()
        await asyncio.to_thread(store.claim, self.conversation_id, self.actor["id"], event.request_id)
        self.request_id = event.request_id
        self.queue = asyncio.Queue(maxsize=128) if isinstance(event, AudioStart) else None
        self.audio_bytes, self.audio_committed = 0, False
        self.task = asyncio.create_task(self.process(event))

    async def audio_chunk(self, chunk):
        if not self.queue or not self.task or self.task.done() or self.audio_committed:
            await self.error(AgentError("audio_state", "请先开始录音，再发送音频。"))
            return
        if len(chunk) > 65536 or len(chunk) % 2:
            await self.error(AgentError("invalid_audio", "音频帧必须是小于等于64 KiB的16-bit PCM数据。"), self.request_id)
            await self.cancel_active()
            return
        self.audio_bytes += len(chunk)
        if self.audio_bytes > MAX_AUDIO_BYTES or self.queue.full():
            code = "audio_too_long" if self.audio_bytes > MAX_AUDIO_BYTES else "audio_backpressure"
            await self.error(AgentError(code, "录音过长或上传积压，请分段提问。"), self.request_id)
            await self.cancel_active()
            return
        self.queue.put_nowait(chunk)

    async def process(self, event):
        request_id = event.request_id
        result_status = "completed"
        failure_code = None
        trace = Trace(store.session_factory, self.conversation_id, self.actor['id'], request_id,
                      type(event).__name__, self.provider.settings.model, SYSTEM_PROMPT, TOOLS)
        trace_token = current_trace.set(trace)
        # 每轮上下文独立；业务线程和模型事件写入同一轨迹，最终在finally中恢复上下文。
        try:
            if isinstance(event, AudioStart):
                await self.emit("audio.ready", request_id, format="pcm_s16le", sample_rate=16000, channels=1)

                async def transcript(text, final):
                    await self.emit("transcript", request_id, text=text, sentence_final=final)

                async with asyncio.timeout(75):
                    text = await self.provider.recognize(self.queue, transcript)
                if not self.audio_committed:
                    raise AgentError("audio_not_committed", "录音尚未提交。")
                text = text[:2000]
            elif isinstance(event, ChatSend):
                text = event.text.strip()
            elif isinstance(event, ActionDecline):
                text = "不确认"
            elif isinstance(event, ActionConfirm):
                text = "确认"
            else:
                text = f"请求人工处理：{event.summary}"
            trace.values['question'] = redacted(text)
            await asyncio.to_thread(trace.finish, 'running')
            begun = await asyncio.to_thread(store.begin_turn, self.conversation_id, self.actor["id"], request_id, text)
            if begun.get("replay"):
                trace.answered(begun['replay'], replay=True)
                await asyncio.to_thread(trace.finish, 'running')
                await self.emit("assistant.message", request_id, message={**begun["replay"], 'trace_id': trace.values['id']}, replay=True)
                return
            await self.emit("user.message", request_id, text=text, source="voice" if isinstance(event, AudioStart) else "text")
            await self.emit("state", request_id, state="thinking")
            word = normalized(text)
            if isinstance(event, ActionConfirm) or word in CONFIRM_WORDS:
                action = await asyncio.to_thread(store.pending_action, self.conversation_id, self.actor["id"])
                action_id = str(event.action_id) if isinstance(event, ActionConfirm) else action["id"] if action else None
                if not action_id:
                    answer = {"text": "当前没有待确认的操作，请先说明要办理的事项。", "speech_text": "当前没有待确认的操作。"}
                else:
                    self.committing = True
                    try:
                        outcome = await business('confirm_cancel_order', store.confirm, self.conversation_id, self.token, action_id, request_id)
                    finally:
                        self.committing = False
                    answer = {"text": f"订单 {outcome['order_number']} 已取消，结果已记录到订单操作历史。",
                              "speech_text": f"订单尾号{outcome['order_number'][-4:]}已取消。", "action_result": outcome}
            elif word in DECLINE_WORDS:
                await business('decline_action', store.decline, self.conversation_id, self.actor["id"])
                answer = {"text": "已撤销待确认操作，订单未作更改。", "speech_text": "已撤销本次操作。"}
            elif isinstance(event, HandoffRequest):
                ticket = await business('create_handoff_ticket', store.execute_tool, self.conversation_id, self.actor["id"], request_id,
                                                "create_handoff_ticket", {"summary": event.summary})
                answer = {"text": "人工工单已保存，当前为待处理状态，尚未接通人工客服。",
                          "speech_text": "已为您创建人工工单。", "ticket": ticket["ticket"]}
            else:
                async def on_tool(name):
                    await self.emit("state", request_id, state="processing", tool=name)
                answer = await self.engine.answer(self.conversation_id, self.actor["id"], request_id,
                                                  begun["history"], self.token, on_tool)
            answer['trace_id'] = trace.values['id']
            # 答案关联轨迹ID，前端反馈可准确指向本次执行，而非整段会话。
            message = await asyncio.to_thread(store.save_answer, self.conversation_id, self.actor["id"], request_id, answer)
            trace.answered(answer)
            await asyncio.to_thread(trace.finish, 'running')
            await self.emit("assistant.message", request_id, message=message)
            for key, kind in (("action", "action.required"), ("action_result", "action.result"), ("ticket", "ticket.created")):
                if key in answer:
                    await self.emit(kind, request_id, **{key: answer[key]})
            if event.speak and answer.get("speech_text") and self.provider.settings.api_key and not self.closed:
                await self.speak(request_id, answer["speech_text"])
        except asyncio.CancelledError:
            result_status = "cancelled"
        except AgentError as error:
            result_status = "failed"
            failure_code = error.code
            await self.error(error, request_id)
        except Exception as error:
            result_status = "failed"
            failure_code = type(error).__name__
            logger.warning("Agent turn failed: %s", type(error).__name__)
            await self.error(AgentError("internal_error", "处理暂时失败，请查看会话记录或稍后重试。"), request_id)
        finally:
            with CancelScope(shield=True):
                try:
                    await asyncio.to_thread(store.release, self.conversation_id, self.actor["id"], request_id, result_status == "cancelled")
                finally:
                    self.queue = None
                    await asyncio.to_thread(trace.finish, result_status, failure_code)
                    # 在turn.done之前完成记录，便于页面结束等待后立即查询本轮状态。
                    current_trace.reset(trace_token)
                    await self.emit("turn.done", request_id, status=result_status)
                    self.request_id, self.task = None, None

    async def speak(self, request_id, text):
        self.speaking = True
        await self.emit("audio.start", request_id, format="pcm_s16le", sample_rate=24000, channels=1)
        completed = False
        try:
            async for chunk in self.provider.synthesize(text):
                if self.closed:
                    return
                await self.socket.send_bytes(chunk)
            completed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.emit("warning", request_id, code="speech_unavailable", message="语音暂时无法播放，请查看文字答案。")
        finally:
            self.speaking = False
            await self.emit("audio.end", request_id, completed=completed)


@router.websocket("/ws")
async def websocket_endpoint(socket: WebSocket, conversation_id: str = ""):
    settings = AgentSettings.from_env()
    origin = socket.headers.get("origin", "").rstrip("/")
    same_origin = f"{'https' if socket.url.scheme == 'wss' else 'http'}://{socket.headers.get('host', '')}"
    if origin and origin not in (*settings.allowed_origins, same_origin):
        await socket.close(code=1008)
        return
    token = socket.cookies.get(COOKIE_NAME)
    try:
        conversation_id = str(UUID(conversation_id))
        actor = await asyncio.to_thread(store.identity, token)
        await asyncio.to_thread(store.conversation, conversation_id, actor["id"])
    except (AgentError, ValueError):
        await socket.close(code=1008)
        return
    await socket.accept()
    await AgentSocket(socket, conversation_id, token, actor, BailianProvider(settings)).run()
