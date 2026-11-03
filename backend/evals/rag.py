# [RAG离线评测] 固定日期与合成政策验证召回、排序及拒答，不调用外部服务。
"""Reproducible retrieval benchmark, synthetic policy only, no external APIs."""
import argparse
from datetime import date, datetime, timezone
import html
import json
from pathlib import Path
from uuid import uuid4
from app.features.knowledge.models import KnowledgeVersion
from app.features.knowledge.service import content_hash, retrieve
from .fixtures import Fixture

AS_OF = date(2026, 11, 2)


def seed(db):
    docs = [
        ('returns-old', 'returns', 'V1', '退货期限政策', '# 退货期限\n退货须在收到商品后7天内申请。', date(2026,1,1), None, 'published'),
        ('returns-current', 'returns', 'V2', '退货期限政策', '# 退货期限\n退货须在收到商品后15天内申请。须提供订单凭据，商品须符合门店验收条件。', date(2026,9,1), None, 'published'),
        ('returns-future', 'returns', 'V3', '退货期限政策', '# 退货期限\n退货须在收到商品后30天内申请。', date(2027,1,1), None, 'published'),
        ('dispatch-current', 'dispatch', 'V1', '发货时效规则', '# 发货时效\n收款后48小时内交接快递。节假日另行通知，时效不等于快递送达承诺。', date(2026,1,1), None, 'published'),
        ('invoice-current', 'invoice', 'V1', '发票申请规则', '# 发票申请\n订单完成后30天内可申请电子发票，需提供抬头和税号。', date(2026,1,1), None, 'published'),
        ('warranty-expired', 'warranty', 'V1', '保修范围', '# 保修期限\n保修期限为一年。', date(2025,1,1), date(2026,1,1), 'published'),
        ('discount-draft', 'discount', 'V1', '优惠券规则', '# 优惠券\n优惠券可无限叠加。', date(2026,1,1), None, 'draft'),
    ]
    for identifier, key, version, title, content, start, end, status in docs:
        db.add(KnowledgeVersion(id=identifier, document_key=key, version=version, title=title, content=content,
            content_hash=content_hash(content), effective_from=start, effective_until=end, status=status,
            created_by='synthetic-eval', published_at=datetime(2026,1,1) if status=='published' else None))
    db.commit()


def tasks():
    groups = [
        ('returns-current', ['退货期限', '退货期限政策是什么', '退货须在收到商品后多久申请', '退货有哪些验收条件', '退货需要订单凭据吗']),
        ('dispatch-current', ['发货时效', '收款后多久交接快递', '发货时效规则', '节假日发货如何处理', '发货时效是否等于送达承诺']),
        ('invoice-current', ['发票申请', '电子发票需提供什么', '订单完成后几天申请发票', '发票抬头和税号', '发票申请规则']),
        (None, ['保修期限', '优惠券叠加规则', '股票收益', '火星移民', '员工工资怎么计算']),
    ]
    return [{'id': f'rag-{i:02}', 'query': q, 'expected_document': expected}
            for i,(expected,q) in enumerate(((e,q) for e,qs in groups for q in qs), 1)]


def evaluate(db):
    # 正样本统计文档召回与排名；负样本要求完全无命中，覆盖过期、草稿和无关问题。
    rows = []
    for case in tasks():
        result = retrieve(db, case['query'], as_of=AS_OF)
        ids = list(dict.fromkeys(d['document_id'] for d in result['documents']))
        expected = case['expected_document']
        rank = ids.index(expected)+1 if expected in ids else None
        rows.append({**case, 'retrieved_documents': ids, 'rank': rank,
                     'passed': bool(rank) if expected else not ids})
    positives = [r for r in rows if r['expected_document']]
    negatives = [r for r in rows if not r['expected_document']]
    return {'dataset': 'synthetic-rag-v1', 'as_of': AS_OF.isoformat(), 'mode': 'local-retrieval-only',
        'summary': {'tasks': len(rows), 'passed': sum(r['passed'] for r in rows),
                    'recall_at_5': sum(r['rank'] is not None for r in positives)/len(positives),
                    'mrr': sum(1/r['rank'] if r['rank'] else 0 for r in positives)/len(positives),
                    'negative_abstention_rate': sum(r['passed'] for r in negatives)/len(negatives)}, 'results': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2]/'.local-test'/'rag-evals')
    args = parser.parse_args()
    fixture = Fixture()
    try:
        with fixture.factory() as db:
            seed(db)
            report = evaluate(db)
    finally:
        fixture.close()
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6])
    data = json.dumps(report, ensure_ascii=False, indent=2)
    path.with_suffix('.json').write_text(data, encoding='utf-8')
    path.with_suffix('.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>RAG检索评测</title><h1>隔离合成政策检索评测</h1><p>不代表真实门店语料质量、模型回答准确率或语义检索能力。</p><pre>'+html.escape(data)+'</pre></html>', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    print(f'Report: {path.with_suffix(".html")}')
    return 0 if all(r['passed'] for r in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
