# [AI 运行接口] 汇总当前账号的轨迹、反馈与复评样例，所有查询均受账号范围约束。
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import statistics
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.auth.models import UserModel
from app.features.operations.models import utcnow
from .telemetry import AgentTrace, redacted

router = APIRouter(prefix='/agent/observability', tags=['agent-observability'])


def scope(actor, days):
    # 账号条件必须放在基础查询中，保证列表、统计和导出沿用相同访问边界。
    return select(AgentTrace).where(AgentTrace.user_id == actor.id, AgentTrace.started_at >= utcnow()-timedelta(days=days))


def summary(rows, total):
    # 仅汇总最近的有界样本；同时返回期间总数，避免把抽样指标当成完整历史统计。
    n = len(rows)
    events = [event for row in rows for event in row.events]
    tools = [e for e in events if e['kind'] == 'tool']
    times = sorted(r.answer_latency_ms for r in rows if r.answer_latency_ms is not None)
    known_usage = [r.token_usage for r in rows if r.token_usage is not None]
    # 未知费用与未知Token不参与累计，页面另外展示已知数据的覆盖条数。
    known_cost = [Decimal(r.cost) for r in rows if r.cost is not None]
    statuses = Counter(r.status for r in rows)
    errors = Counter(r.error_code for r in rows if r.error_code)
    feedback = Counter(r.feedback for r in rows if r.feedback)
    return {'total_in_period': total, 'sampled_requests': n, 'sample_limit': 2000,
        'completed_rate': statuses['completed']/n if n else None, 'statuses': dict(statuses),
        'tool_success_rate': sum(t['status']=='ok' for t in tools)/len(tools) if tools else None,
        'tool_calls': len(tools), 'model_calls': sum(e['kind']=='model' for e in events),
        'answer_p50_ms': statistics.median(times) if times else None,
        'answer_p95_ms': times[max(0,(95*len(times)+99)//100-1)] if times else None,
        'known_tokens': sum(u['total_tokens'] for u in known_usage), 'usage_known_requests': len(known_usage),
        'known_cost': str(sum(known_cost, Decimal(0))), 'cost_known_requests': len(known_cost), 'currency': 'CNY',
        'abstentions': sum(r.grounding=='abstained' for r in rows),
        'extractive_fallbacks': sum(r.grounding=='extractive_fallback' for r in rows),
        'replayed_requests': sum(r.replayed for r in rows), 'feedback': dict(feedback), 'errors': dict(errors)}


@router.get('')
def overview(days: int = Query(7,ge=1,le=90), db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    statement = scope(actor, days)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(AgentTrace.started_at.desc()).limit(2000)).all()
    return summary(rows, total)


@router.get('/traces')
def traces(days: int = Query(7,ge=1,le=90), status: str = '', feedback: str = '', page: int = Query(1,ge=1),
           db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    statement = scope(actor, days)
    if status:
        statement = statement.where(AgentTrace.status == status)
    if feedback:
        statement = statement.where(AgentTrace.feedback == feedback)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.order_by(AgentTrace.started_at.desc()).offset((page-1)*20).limit(20)).all()
    return {'total': total, 'page': page, 'items': [{key:getattr(r,key) for key in ('id','started_at','status','question',
        'model','answer_latency_ms','duration_ms','error_code','grounding','feedback','replayed')} for r in rows]}


def owned(db, actor, trace_id, lock=False):
    # 查详情与写反馈共用归属校验；不存在和越权统一返回404。
    statement = select(AgentTrace).where(AgentTrace.id == trace_id, AgentTrace.user_id == actor.id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(statement)
    if not row:
        raise HTTPException(404,'请求轨迹不存在或无权访问')
    return row


@router.get('/traces/{trace_id}')
def detail(trace_id: str, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    row = owned(db, actor, trace_id)
    return {column.name: getattr(row,column.name) for column in AgentTrace.__table__.columns if column.name != 'user_id'}


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rating: str = Field(pattern=r'^(helpful|unhelpful)$')
    note: str = Field(default='',max_length=500)


@router.put('/traces/{trace_id}/feedback')
def feedback(trace_id: str, payload: Feedback, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # 锁定后覆盖本条反馈，重复点击不会被累计成多张评价票。
    row = owned(db, actor, trace_id, True)
    row.feedback, row.feedback_note = payload.rating, redacted(payload.note,500)
    db.commit()
    return {'rating':row.feedback}


@router.get('/traces/{trace_id}/evaluation-case')
def evaluation_case(trace_id: str, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # 导出的是待人工核对的观察记录；不能直接作为标准答案，更不会自动重放业务写入。
    row = owned(db, actor, trace_id)
    return {'schema':'agent-badcase-review-v1', 'source_trace': row.id, 'prompt':row.question,
        'observed_answer':row.answer, 'observed_tools':[e for e in row.events if e['kind']=='tool'],
        'model':row.model,'prompt_hash':row.prompt_hash,'tools_hash':row.tools_hash,
        'requires_review':True,'notice':'脱敏失败样例，观察结果不是标准答案。人工核对上下文、合成业务夹具和预期工具后再加入评测集；不会自动重放原业务写入。'}
