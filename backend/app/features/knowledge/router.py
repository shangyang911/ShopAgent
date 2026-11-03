# [知识库接口] 管理草稿、发布和撤回，检索只使用当前生效且相关的知识片段。
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.auth.models import UserModel
from app.features.operations.models import utcnow
from .models import KnowledgeVersion
from .service import content_hash, chunks, retrieve, active_versions, today

router = APIRouter(prefix='/knowledge', tags=['knowledge'], dependencies=[Depends(require_current_user)])


class DocumentInput(BaseModel):
    # 禁止额外字段，防止客户端越过生命周期接口直接指定已发布状态。
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    document_key: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    version: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=10, max_length=50000)
    effective_from: date
    effective_until: date | None = None

    @model_validator(mode='after')
    def valid_period(self):
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError('失效日期不能早于生效日期')
        if not chunks(self.content):
            raise ValueError('正文不可仅包含标题')
        return self


def wire(row, active_ids=(), full=False):
    # 列表不携带大段正文；详情才返回内容，同时明确当前是否适用。
    result = {key: getattr(row,key) for key in ('id','document_key','version','title','effective_from','effective_until',
        'status','content_hash','created_by','created_at','published_at','withdrawn_at')}
    result['currently_effective'] = row.id in active_ids
    result['chunk_count'] = len(chunks(row.content))
    if full:
        result['content'] = row.content
    return result


@router.get('')
def listing(db: Session = Depends(get_db)):
    active = {row.id for row in active_versions(db)}
    rows = db.scalars(select(KnowledgeVersion).order_by(KnowledgeVersion.created_at.desc()).limit(200)).all()
    return {'items': [wire(r, active) for r in rows], 'as_of': today(), 'limit': 200}


@router.post('', status_code=201)
def create(payload: DocumentInput, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # 文档标识+版本号唯一，重复提交不能覆盖旧版本；新内容统一先保存为草稿。
    if db.scalar(select(KnowledgeVersion.id).limit(2000).offset(1999)):
        raise HTTPException(409, '本地知识库已达2000版本上限，请归档规划后再导入')
    row = KnowledgeVersion(**payload.model_dump(), content_hash=content_hash(payload.content), created_by=actor.username)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, '同一文档标识与版本已存在，请使用新版本号') from error
    return wire(row, full=True)


@router.get('/search')
def search(query: str = Query(min_length=1, max_length=500), db: Session = Depends(get_db)):
    return retrieve(db, query)


@router.get('/{document_id}')
def detail(document_id: str, db: Session = Depends(get_db)):
    row = db.get(KnowledgeVersion, document_id)
    if not row:
        raise HTTPException(404, '知识版本不存在')
    return wire(row, {r.id for r in active_versions(db)}, full=True)


@router.post('/{document_id}/{operation}')
def change(document_id: str, operation: str, db: Session = Depends(get_db)):
    # 状态切换先锁行；发布和撤回保留时间，撤回版本不能直接恢复为已发布。
    row = db.scalar(select(KnowledgeVersion).where(KnowledgeVersion.id == document_id).with_for_update())
    if not row:
        raise HTTPException(404, '知识版本不存在')
    if operation == 'publish' and row.status == 'draft':
        if row.effective_until and row.effective_until < today():
            raise HTTPException(409, '已过期版本不能发布')
        row.status, row.published_at = 'published', utcnow()
    elif operation == 'withdraw' and row.status == 'published':
        row.status, row.withdrawn_at = 'withdrawn', utcnow()
    else:
        raise HTTPException(409, '当前状态不允许此操作；修改内容请创建新版本')
    db.commit()
    return wire(row, {r.id for r in active_versions(db)}, full=True)
