"""百炼 HTTP 工具调用、Fun-ASR / CosyVoice WebSocket 任务协议。"""
import asyncio
from contextlib import suppress
import json
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

from .settings import AgentSettings


class AgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BailianProvider:
    def __init__(self, settings: AgentSettings):
        self.settings = settings

    def require_key(self):
        if not self.settings.api_key:
            raise AgentError("service_unconfigured", "智能客服尚未启用，请联系管理员配置服务。")

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    async def _post(self, url, body):
        self.require_key()
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(url, headers=self.headers, json=body)
            if response.status_code in (401, 403):
                raise AgentError("provider_auth", "智能客服服务认证失败，请联系管理员。")
            if response.status_code == 429:
                raise AgentError("provider_busy", "服务繁忙，请稍后重试。")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as from_error:
            raise AgentError("provider_unavailable", "智能客服服务暂时不可用，请稍后重试。") from from_error

    async def complete(self, messages, tools):
        # 同连接可处理多轮请求，每次先清空用量，防止失败调用继承上一轮Token。
        self.last_usage = None
        data = await self._post(f"{self.settings.base_url}/chat/completions", {
            "model": self.settings.model, "messages": messages, "tools": tools,
            "temperature": 0.2, "max_tokens": 2200, "enable_thinking": False,
        })
        # 只保留供应商返回的用量；未提供时保持未知，不用字符数伪造 token。
        self.last_usage = data.get("usage")
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise AgentError("provider_response", "智能客服返回异常，请重试。") from error

    async def retrieve(self, query):
        if not self.settings.knowledge_base_id or not self.settings.rag_url:
            return {"configured": False, "documents": [], "notice": "尚未接入门店知识库，不能确认退换货政策、时效或承诺。"}
        data = await self._post(self.settings.rag_url, {
            "index_id": self.settings.knowledge_base_id, "query": query, "top_k": 5,
        })
        output = data.get("output", data.get("data", data))
        nodes = output.get("nodes", [])
        documents = []
        for index, node in enumerate(nodes[:5]):
            document = node.get("document", node)
            text = document.get("text") or node.get("text") or ""
            metadata = document.get("metadata") or node.get("metadata") or {}
            if text:
                # 云端证据必须明确版本、有效期和当前版本标识，不能从Top-K结果猜测适用性。
                from datetime import date
                from app.features.knowledge.service import today
                try:
                    valid_from = date.fromisoformat(metadata['effective_from'])
                    valid_until = date.fromisoformat(metadata['effective_until']) if metadata.get('effective_until') else None
                except (KeyError, ValueError, TypeError):
                    continue
                if not metadata.get('version') or metadata.get('is_current') is not True or valid_from > today() or (valid_until and valid_until < today()):
                    continue
                documents.append({"id": str(node.get("id", index)), "text": str(text)[:2500],
                    "title": str(metadata.get("title") or metadata.get("file_name") or "知识库文档")[:200],
                    'version': str(metadata['version']), 'effective_from': valid_from.isoformat(),
                    'effective_until': valid_until.isoformat() if valid_until else None,
                    'section': str(metadata.get('section', '正文')), 'content_hash': metadata.get('content_hash')})
        return {"configured": True, "documents": documents}

    def _connect(self):
        self.require_key()
        return connect(self.settings.ws_url, additional_headers=self.headers,
                       open_timeout=12, close_timeout=3, max_size=2 * 1024 * 1024)

    @staticmethod
    def _command(action, task_id, payload):
        return json.dumps({"header": {"action": action, "task_id": task_id, "streaming": "duplex"},
                           "payload": payload}, ensure_ascii=False)

    @staticmethod
    def _event(raw):
        event = json.loads(raw)
        if event.get("header", {}).get("event") == "task-failed":
            # 不把供应商原始报文、请求头或凭证传给浏览器。
            raise AgentError("speech_provider", "语音服务暂时不可用，请使用文字或稍后重试。")
        return event

    async def _started(self, socket):
        async with asyncio.timeout(15):
            while True:
                event = self._event(await socket.recv())
                if event.get("header", {}).get("event") == "task-started":
                    return

    async def recognize(self, queue, on_transcript):
        """接收 16 kHz、单声道、16-bit PCM；只有客户端提交后才结束任务。"""
        task_id = uuid4().hex
        async with asyncio.timeout(90), self._connect() as socket:
            await socket.send(self._command("run-task", task_id, {
                "task_group": "audio", "task": "asr", "function": "recognition",
                "model": self.settings.asr_model,
                "parameters": {"format": "pcm", "sample_rate": 16000}, "input": {},
            }))
            await self._started(socket)

            async def send_audio():
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        await socket.send(self._command("finish-task", task_id, {"input": {}}))
                        return
                    await socket.send(chunk)

            sender = asyncio.create_task(send_audio())
            sentences = {}
            try:
                while True:
                    receive = asyncio.create_task(socket.recv())
                    done, _ = await asyncio.wait({receive, sender}, return_when=asyncio.FIRST_COMPLETED)
                    if sender in done and sender.exception():
                        receive.cancel()
                        with suppress(asyncio.CancelledError):
                            await receive
                        raise sender.exception()
                    raw = await receive
                    event = self._event(raw)
                    output = event.get("payload", {}).get("output", {})
                    sentence = output.get("sentence") or {}
                    text = sentence.get("text", "")
                    if text:
                        key = sentence.get("begin_time", len(sentences))
                        final = bool(sentence.get("sentence_end"))
                        if final:
                            sentences[key] = text
                        await on_transcript("".join(sentences.values()) + ("" if final else text), final)
                    if event.get("header", {}).get("event") == "task-finished":
                        await sender
                        result = "".join(sentences.values()).strip()
                        if not result:
                            raise AgentError("no_speech", "没有识别到清晰语音，请重试或输入文字。")
                        return result
            finally:
                sender.cancel()
                if 'receive' in locals() and not receive.done():
                    receive.cancel()
                    with suppress(asyncio.CancelledError):
                        await receive
                with suppress(asyncio.CancelledError):
                    await sender

    async def synthesize(self, text):
        """逐帧输出 PCM s16le / 24 kHz 音频；文本摘要由 Agent 单独提供。"""
        task_id = uuid4().hex
        async with asyncio.timeout(45), self._connect() as socket:
            await socket.send(self._command("run-task", task_id, {
                "task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                "model": self.settings.tts_model,
                "parameters": {"text_type": "PlainText", "voice": self.settings.voice,
                               "format": "pcm", "sample_rate": 24000, "volume": 50,
                               "rate": 1, "pitch": 1}, "input": {},
            }))
            await self._started(socket)
            await socket.send(self._command("continue-task", task_id, {"input": {"text": text}}))
            await socket.send(self._command("finish-task", task_id, {"input": {}}))
            async for raw in socket:
                if isinstance(raw, bytes):
                    yield raw
                elif self._event(raw).get("header", {}).get("event") == "task-finished":
                    return
            raise AgentError("speech_incomplete", "语音播放中断，请查看文字答案。")
