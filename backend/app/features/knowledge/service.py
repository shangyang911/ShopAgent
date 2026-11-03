# [本地知识检索] 组合BM25与字符向量相似度，适用于可离线复现的字面检索。
"""Offline lexical/character-vector retrieval; no claim of neural semantic embeddings."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
import unicodedata
from sqlalchemy import select
from .models import KnowledgeVersion


def today():
    # 政策生效日按北京时间判断，与数据库保存的UTC事件时间分开处理。
    return datetime.now(timezone(timedelta(hours=8))).date()


def normalize(text):
    # 统一全角/半角和英文大小写，减少同一词因输入形式不同而漏检。
    return unicodedata.normalize('NFKC', text).lower()


def terms(text):
    # 中文按相邻双字切词，英文与数字保留完整词项，供BM25统计词频。
    text = normalize(text)
    result = re.findall(r'[a-z0-9]+', text)
    for part in re.findall(r'[\u4e00-\u9fff]+', text):
        result.extend(part[i:i+2] for i in range(len(part)-1))
        if len(part) == 1:
            result.append(part)
    return result


def vector_terms(text):
    # 三字符片段构建TF-IDF向量；它是字面特征，不是神经网络语义Embedding。
    text = ''.join(re.findall(r'[\w]+', normalize(text)))
    return [text[i:i+3] for i in range(len(text)-2)]


def chunks(content, size=600, overlap=80):
    # 保留章节标题；长正文按重叠窗口切分，尽量避免条款条件被切断后丢失。
    heading = '正文'
    sections = []
    for line in content.replace('\r\n', '\n').split('\n'):
        if re.match(r'^#{1,6}\s+', line):
            heading = line.lstrip('#').strip()
        elif line.strip():
            sections.append((heading, line.strip()))
    grouped = []
    for heading, line in sections:
        if grouped and grouped[-1][0] == heading:
            grouped[-1] = (heading, grouped[-1][1]+'\n'+line)
        else:
            grouped.append((heading, line))
    output = []
    for heading, text in grouped:
        for start in range(0, len(text), size-overlap):
            piece = text[start:start+size]
            output.append({'section': heading, 'text': piece})
            if start+size >= len(text):
                break
    return output


def active_versions(db, as_of=None):
    as_of = as_of or today()
    # 已开始生效的新版本会替代旧版，即使新版后来过期或撤回，也不自动恢复旧政策。
    # 草稿和未来版本不提前遮盖当前适用政策。
    rows = db.scalars(select(KnowledgeVersion).where(KnowledgeVersion.status.in_(['published', 'withdrawn']),
        KnowledgeVersion.effective_from <= as_of).order_by(KnowledgeVersion.effective_from.desc(),
        KnowledgeVersion.published_at.desc(), KnowledgeVersion.id)).all()
    selected = {}
    for row in rows:
        selected.setdefault(row.document_key, row)
    return [row for row in selected.values() if row.status == 'published' and
            (row.effective_until is None or row.effective_until >= as_of)]


def retrieve(db, query, top_k=5, as_of=None):
    as_of = as_of or today()
    versions = active_versions(db, as_of)
    docs = []
    # 引用ID绑定版本和段号，附带内容指纹、日期与原文入口，便于回答保存证据快照。
    for version in versions:
        for index, chunk in enumerate(chunks(version.content)):
            docs.append({**chunk, 'id': f'{version.id}:{index}', 'document_id': version.id, 'document_key': version.document_key,
                'title': version.title, 'version': version.version, 'effective_from': version.effective_from.isoformat(),
                'effective_until': version.effective_until.isoformat() if version.effective_until else None,
                'content_hash': version.content_hash, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'href': f'/knowledge?document_id={version.id}'})
    if not docs:
        return {'configured': bool(versions), 'documents': [], 'notice': '没有可用的当前生效政策，无法确认，请核实后发布知识文档。'}
    query_tokens = Counter(terms(query))
    # 排除“期限”等泛词单独命中，避免过期保修问题误召回退货期限。
    generic = {'期限', '规则', '政策', '申请', '提供', '如何', '什么', '是否', '多少', '几天', '多久', '可以'}
    substantive = set(query_tokens)-generic
    corpus = [Counter(terms(d['title']+' '+d['section']+' '+d['text'])) for d in docs]
    n = len(docs)
    df = Counter(token for c in corpus for token in c)
    avg = sum(sum(c.values()) for c in corpus)/n or 1
    bm25 = []
    # BM25用词频与长度归一化衡量匹配；查询覆盖度用于过滤仅有少量泛词重叠的片段。
    coverage = []
    for c in corpus:
        length = sum(c.values())
        bm25.append(sum(math.log(1+(n-df[t]+.5)/(df[t]+.5))*c[t]*2.2/(c[t]+1.2*(.25+.75*length/avg))
                         for t in query_tokens if c[t]))
        coverage.append(len(set(c)&set(query_tokens))/max(1, len(query_tokens)))
    vectors = [Counter(vector_terms(d['title']+' '+d['section']+' '+d['text'])) for d in docs]
    # 使用当前生效语料计算IDF，再通过余弦相似度比较查询与正文字符向量。
    vdf = Counter(t for v in vectors for t in v)
    def weights(tokens):
        return {t: count*(math.log((n+1)/(vdf[t]+1))+1) for t,count in tokens.items()}
    qv = weights(Counter(vector_terms(query)))
    qnorm = math.sqrt(sum(x*x for x in qv.values())) or 1
    cosine = []
    for v in vectors:
        values = weights(v)
        norm = math.sqrt(sum(x*x for x in values.values())) or 1
        cosine.append(sum(qv.get(t,0)*x for t,x in values.items())/qnorm/norm)
    ranks = []
    for scores in (bm25, cosine):
        ranks.append({i: rank+1 for rank,i in enumerate(sorted(range(n), key=lambda i: (-scores[i], docs[i]['id']))) if scores[i]>0})
    ranked = []
    for i, doc in enumerate(docs):
        if bm25[i] <= 0 or coverage[i] < .2 or not (substantive & set(corpus[i])):
            continue
        # RRF融合两路排名，再按查询覆盖度重排，避免直接混合不同量纲的原始得分。
        score = sum(1/(60+r[i]) for r in ranks if i in r)*(.5+.5*coverage[i])
        ranked.append({**doc, 'score': round(score, 6), 'query_coverage': round(coverage[i], 3)})
    ranked.sort(key=lambda d: (-d['score'], d['id']))
    return {'configured': True, 'documents': ranked[:top_k], 'as_of': as_of.isoformat(),
            'method': 'BM25 + character-trigram TF-IDF cosine + RRF + coverage rerank',
            'notice': '仅当前生效且已发布的政策；引用为检索证据，不保证模型解释完全正确。' if ranked else '未检索到相关政策，无法确认。'}


def content_hash(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()
