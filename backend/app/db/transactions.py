"""已知整笔回滚的数据库死锁可重试；外部调用或外层持有的事务不能在这里重放。"""
from functools import wraps

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError


def retry_deadlocks(operation):
    """用于自行提交、无外部副作用的服务；commit=False 时由外层负责整笔重试。"""
    @wraps(operation)
    def execute(db, *args, **kwargs):
        for attempt in range(3):
            try:
                return operation(db, *args, **kwargs)
            except OperationalError as error:
                # 1213 确认 MySQL 已回滚整笔事务；锁等待超时、连接中断不作自动重试。
                if (kwargs.get('commit') is False or db.get_bind().dialect.name != 'mysql'
                    or not error.orig.args or error.orig.args[0] != 1213):
                    raise
                db.rollback()
                if attempt == 2:
                    raise HTTPException(409, '业务并发处理冲突，请稍后使用原提交内容重试') from error
    return execute
