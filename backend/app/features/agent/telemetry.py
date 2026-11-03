# [AI 可观测性] 按请求记录调用链；使用独立事务，记录失败不影响原业务结果。
"""Request-scoped telemetry. Recording failures never change business outcomes."""
from contextvars import ContextVar
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
import re
import time
import asyncio
from uuid import uuid4
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.features.operations.models import utcnow

logger = logging.getLogger(__name__)
current_trace = ContextVar('agent_trace', default=None)
# ContextVar 隔离并发请求；asyncio.to_thread 会复制上下文，使业务线程仍能关联本轮轨迹。


class AgentTrace(Base):
    # 一次执行尝试对应一条轨迹；同 request_id 的重放也有独立轨迹 ID，避免覆盖原记录。
    __tablename__ = 'agent_traces'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    input_type: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text, default='')
    answer: Mapped[str] = mapped_column(Text, default='')
    model: Mapped[str] = mapped_column(String(100))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    tools_hash: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    answer_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    grounding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    replayed: Mapped[bool] = mapped_column(default=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost: Mapped[str | None] = mapped_column(String(40), nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)
    feedback_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


def fingerprint(value):
    # 仅记录提示词和工具定义的稳定指纹，用于对比版本，不复制完整提示词内容。
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def redacted(value, limit=600):
    # 先做常见凭证、号码与邮箱脱敏，再限长；这是基础防护，不等于完整敏感信息识别。
    value = str(value)
    value = re.sub(r'(?i)(?:sk-[\w-]+|Bearer\s+\S+|[\w.+-]+@[\w.-]+\.[a-z]{2,})', '[已隐藏]', value)
    value = re.sub(r'(?<!\d)\d{7,}(?!\d)', '[号码已隐藏]', value)
    value = re.sub(r'(?i)((?:api[\s_-]?key|token|secret|password|cookie|密钥)\s*[:=：]\s*)\S+', r'\1[已隐藏]', value)
    return value[:limit]


def event(kind, **values):
    # 单轮最多保留100个事件，防止异常循环使诊断记录无限增长。
    trace = current_trace.get()
    if trace is not None and len(trace.events) < 100:
        trace.events.append({'kind': kind, **values})


async def complete(provider, messages, tools):
    # 用单调时钟测量真实调用耗时；Token只采用供应商返回值，异常时保持未知。
    start = time.perf_counter()
    try:
        result = await provider.complete(messages, tools)
        raw = getattr(provider, 'last_usage', None)
        usage = {k: raw[k] for k in ('prompt_tokens','completion_tokens','total_tokens') if type(raw.get(k)) is int} if isinstance(raw,dict) else None
        event('model', status='ok', latency_ms=(time.perf_counter()-start)*1000, usage=usage)
        return result
    except BaseException as error:
        event('model', status='cancelled' if isinstance(error, asyncio.CancelledError) else 'error',
              latency_ms=(time.perf_counter()-start)*1000, usage=None,
              error_code=getattr(error, 'code', type(error).__name__))
        raise


async def business(name, function, *args):
    # 确认、撤销等绕过模型直接执行的操作，也纳入同一请求调用链。
    start = time.perf_counter()
    status = 'error'
    try:
        result = await asyncio.to_thread(function, *args)
        status = 'ok'
        return result
    finally:
        event('tool', name=name, status=status, arguments={}, latency_ms=(time.perf_counter()-start)*1000)


def safe_arguments(args):
    # 参数采用白名单，只保留定位问题所需的查询条件，避免写入整份订单或取消原因。
    return {key: redacted(value, 180) if isinstance(value, str) else value
            for key,value in args.items() if key in {'query','keyword','order_id','product_id','sku_code','color','size','active_only','limit','status','coverage_days','minimum_stock','safety_percent'}}


def usage_and_cost(events):
    # 没有模型调用属于确定的零消耗；调用过但缺少用量属于未知，不能当作零。
    calls = [e for e in events if e['kind'] == 'model']
    if not calls:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, '0'
    required = ('prompt_tokens', 'completion_tokens')
    if any(not isinstance(e.get('usage'), dict) or any(type(e['usage'].get(k)) is not int or e['usage'][k] < 0 for k in required) for e in calls):
        return None, None
    usage = {k: sum(e['usage'][k] for e in calls) for k in required}
    usage['total_tokens'] = usage['prompt_tokens']+usage['completion_tokens']
    try:
        incoming = Decimal(os.environ['AGENT_INPUT_PRICE_PER_MILLION'])
        outgoing = Decimal(os.environ['AGENT_OUTPUT_PRICE_PER_MILLION'])
        if not incoming.is_finite() or not outgoing.is_finite() or min(incoming,outgoing) < 0:
            return usage, None
        cost = (incoming*usage['prompt_tokens']+outgoing*usage['completion_tokens'])/1000000
        return usage, str(cost.quantize(Decimal('.000001')))
    except (KeyError, InvalidOperation):
        return usage, None


class Trace:
    def __init__(self, factory, conversation_id, user_id, request_id, input_type, model, prompt, tools):
        self.factory, self.started = factory, time.perf_counter()
        self.events = []
        self.values = dict(id=str(uuid4()), conversation_id=conversation_id, user_id=user_id, request_id=request_id,
            input_type=input_type, model=model, prompt_hash=fingerprint(prompt), tools_hash=fingerprint(tools),
            started_at=utcnow(), question='', answer='', answer_latency_ms=None, replayed=False, grounding=None)
    def answered(self, answer, replay=False):
        # 回答耗时截至答案保存前后的检查点，不包含后续语音播报时间。
        self.values.update(answer=redacted(answer.get('text','')), answer_latency_ms=(time.perf_counter()-self.started)*1000,
                           grounding=answer.get('grounding'), replayed=replay)
    def finish(self, status, error_code=None):
        # 同一轨迹可先保存处理中快照，再更新最终状态；不触碰用户已提交的反馈字段。
        try:
            usage, cost = usage_and_cost(self.events)
            with self.factory() as db:
                values = dict(**self.values, events=list(self.events), status=status, error_code=error_code,
                              duration_ms=(time.perf_counter()-self.started)*1000, token_usage=usage, cost=cost)
                row = db.get(AgentTrace, self.values['id'])
                if row is None:
                    db.add(AgentTrace(**values))
                else:
                    for key,value in values.items():
                        setattr(row,key,value)
                db.commit()
        except Exception as error:
            logger.warning('Telemetry persistence failed: %s', type(error).__name__)
