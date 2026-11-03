"""Immutable picking snapshots and explicit, idempotent print intentions."""
from copy import deepcopy
from html import escape
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.features.orders.models import OrderModel, OrderStatus
from .common import add_event, digest, wire
from .models import PrintDocument, PrintJob, utcnow
from .print_controls import print_controls


def request_print(db, payload, actor):
    """记录一次明确的打印意图并冻结单据；相同键重试复用原任务，但仍校验当前订单。"""
    if set(payload.source_documents) - set(payload.order_ids):
        raise HTTPException(422, '原版文档必须属于本次选择的订单')
    hashed = digest(payload.model_dump(exclude={"idempotency_key"}))
    previous = db.scalar(select(PrintJob).where(PrintJob.idempotency_key == payload.idempotency_key))
    if previous:
        if previous.payload_hash != hashed:
            raise HTTPException(409, "打印幂等键已用于其他内容")
        states = list(db.execute(select(OrderModel.id, OrderModel.status).where(OrderModel.id.in_(payload.order_ids)).order_by(OrderModel.id).with_for_update()))
        if len(states) != len(set(payload.order_ids)) or any(state in (OrderStatus.CANCELLED, OrderStatus.REFUNDED) for _, state in states):
            raise HTTPException(409, '订单已取消、退款或不存在，原打印请求不能再次执行')
        return previous
    # Claim the request key before waiting on order locks. A simultaneous retry waits on
    # this unique insert and replays after rollback, rather than being treated as a reprint.
    job = PrintJob(idempotency_key=payload.idempotency_key, payload_hash=hashed, requested_by=actor.username,
                   requested_at=utcnow(), reprint_reason=payload.reprint_reason.strip() or None)
    db.add(job)
    db.flush()
    ids = sorted(set(payload.order_ids))
    orders = list(db.scalars(select(OrderModel).where(OrderModel.id.in_(ids)).order_by(OrderModel.id).with_for_update().execution_options(populate_existing=True)))
    by_id = {order.id: order for order in orders}
    conflicts, prepared = [], []
    for order_id in ids:
        order = by_id.get(order_id)
        if order is None:
            conflicts.append(f"#{order_id} 不存在")
            continue
        previous_doc = db.scalar(select(PrintDocument).where(PrintDocument.order_id == order.id).order_by(PrintDocument.attempt_no.desc()).limit(1).with_for_update())
        source_id = payload.source_documents.get(order.id)
        source = db.get(PrintDocument, source_id) if source_id else None
        if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
            conflicts.append(f"{order.order_number} 已取消或退款")
        elif source_id and (source is None or source.order_id != order.id):
            conflicts.append(f"{order.order_number} 原版文档不匹配")
        elif order.status != OrderStatus.PAID_PENDING_SHIPMENT and not source:
            conflicts.append(f"{order.order_number} 当前状态仅允许补打已有版本")
        elif previous_doc and not payload.reprint_reason.strip():
            conflicts.append(f"{order.order_number} 已请求过打印，必须填写补打原因")
        if source:
            snapshot = deepcopy(source.snapshot)
            revision = source.document_revision
        else:
            snapshot = wire({"order_number": order.order_number, "created_at": order.facts.created_at if order.facts else None,
                             "paid_at": order.paid_at, "recipient_name": order.recipient_name, "recipient_phone": order.recipient_phone,
                             "address": f"{order.country} {order.province}{order.city}{order.district}{order.address_detail}",
                             "items": [{"product_id": item.product_id, "name": item.product_name, "quantity": item.quantity,
                                        "sku_id": item.sku_id, "sku_code": item.sku_code, "sku_label": item.sku_label,
                                        **({"unit_price": str(item.unit_price), "line_total": str(item.line_total)} if payload.include_amount else {})} for item in order.items],
                             "distinct_product_count": len({item.product_id if item.product_id is not None else ('deleted', item.product_name) for item in order.items}), "total_quantity": sum(item.quantity for item in order.items),
                             "picking_note": payload.picking_note, "include_amount": payload.include_amount,
                             **({"total": str(order.total), "shipping_fee": str(order.shipping_fee)} if payload.include_amount else {})})
            same = db.scalar(select(PrintDocument).where(PrintDocument.order_id == order.id, PrintDocument.document_hash == digest(snapshot)).order_by(PrintDocument.attempt_no).limit(1).with_for_update())
            highest = db.scalar(select(PrintDocument.document_revision).where(PrintDocument.order_id == order.id).order_by(PrintDocument.document_revision.desc()).limit(1).with_for_update()) or 0
            revision = same.document_revision if same else highest + 1
        prepared.append((order, snapshot, revision, (previous_doc.attempt_no + 1) if previous_doc else 1))
    if conflicts:
        raise HTTPException(409, "整批未生成：" + "；".join(conflicts))
    now = utcnow()
    job.requested_at = now
    for order, snapshot, revision, attempt in prepared:
        document = PrintDocument(job_id=job.id, order_id=order.id, attempt_no=attempt, document_revision=revision,
                                 document_hash=digest(snapshot), snapshot=snapshot)
        db.add(document)
        db.flush()
        add_event(db, order, "reprint_requested" if attempt > 1 else "print_requested", "请求补打" if attempt > 1 else "请求打印", actor,
                  key=f"print:{job.id}:{order.id}", reason=job.reprint_reason, occurred_at=now,
                  details={"print_job_id": job.id, "document_id": document.id, "attempt_no": attempt, "document_hash": document.document_hash,
                           "layout": payload.layout})
    db.commit()
    return job


def render_print(db, job, document_id=None, *, layout='orders', api_base='/api/order-operations'):
    def e(value):
        return escape(str(value if value is not None else "待核实"))

    def business_time(value):
        if value is None:
            return '时间待核实'
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + '（上海）'

    sources = []
    statement = select(PrintDocument).where(PrintDocument.job_id == job.id).order_by(PrintDocument.id)
    if document_id is not None:
        statement = statement.where(PrintDocument.id == document_id)
    documents = list(db.scalars(statement))
    status_labels = {"pending_payment": "待付款", "paid_pending_shipment": "待发货", "in_delivery": "配送中", "completed": "已完成", "cancelled": "已取消 · 单据失效", "refunded": "已退款 · 单据失效"}
    for doc in documents:
        snapshot = doc.snapshot
        order = db.get(OrderModel, doc.order_id)
        status = status_labels.get(order.status.value, order.status.value) if order else "订单不存在"
        rows = []
        for item in snapshot["items"]:
            amount = f"<td>{e(item.get('line_total'))}</td>" if snapshot["include_amount"] else ""
            rows.append(f"<tr><td>□</td><td>{e(item['product_id'])}</td><td>{e(item['name'])}<br>{e(item.get('sku_code') or '')} {e(item.get('sku_label') or '')}</td><td>{item['quantity']} 件</td>{amount}</tr>")
        money_head = '<th style="width:15%">商品金额 / 元</th>' if snapshot['include_amount'] else ''
        total = f"<p>应收（含运费）：¥{e(snapshot['total'])} · 运费：¥{e(snapshot['shipping_fee'])}</p>" if snapshot['include_amount'] else ''
        sources.append(f"""<template class="document-source"><header><h1>配货单</h1><strong>{e(snapshot['order_number'])}</strong>
          <p><span data-order-id="{doc.order_id}">{e(status)}</span> · {'补打' if doc.attempt_no > 1 else '首次请求'} · 请求第 {doc.attempt_no} 次 · 版本 {doc.document_revision}</p></header>
          <div class="meta"><p>收件人：{e(snapshot['recipient_name'])}　电话：{e(snapshot['recipient_phone'])}</p><p>地址：{e(snapshot['address'])}</p>
          <p>创建：{e(business_time(snapshot['created_at']))}　确认收款：{e(business_time(snapshot['paid_at']))}</p></div>
          <table><thead><tr><th style="width:7%">配货</th><th style="width:10%">商品 ID</th><th>商品名称快照</th><th style="width:12%">数量</th>{money_head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
          <div class="tail"><p>{snapshot['distinct_product_count']} 种商品 · 共 {snapshot['total_quantity']} 件</p>{total}
          <p class="note">配货备注：{e(snapshot['picking_note'])}</p><p>配货签字：________________　复核签字：________________</p></div>
          <footer>{e(snapshot['order_number'])} · 本单第 <span class="page-no"></span> / <span class="page-count"></span> 页<br>
          请求人 {e(job.requested_by)} · {e(business_time(job.requested_at))} · 文档 #{doc.id} · {e(doc.document_hash[:16])}<br>
          仅记录打印请求，不能确认实际出纸</footer></template>""")
    if layout == 'summary':
        merged, order_labels, notes = {}, [], []
        for doc in documents:
            order = db.get(OrderModel, doc.order_id)
            snapshot = doc.snapshot
            status = status_labels.get(order.status.value, order.status.value) if order else '订单不存在'
            order_labels.append(f"{e(snapshot['order_number'])}（<span data-order-id='{doc.order_id}'>{e(status)}</span>，请求第 {doc.attempt_no} 次）")
            if snapshot['picking_note']:
                notes.append(f"{snapshot['order_number']}：{snapshot['picking_note']}")
            for item in snapshot['items']:
                key = (item['product_id'], item['name'], item.get('sku_code') or '', item.get('sku_label') or '')
                merged[key] = merged.get(key, 0) + item['quantity']
        rows = ''.join(f'<tr><td>□</td><td>{e(pid)}</td><td>{e(name)}<br>{e(code)} {e(label)}</td><td>{quantity} 件</td></tr>' for (pid, name, code, label), quantity in merged.items())
        notes_text = '\n'.join(notes) or '无'
        sources = [f"""<template class="document-source"><header><h1>合并拣货汇总单</h1><strong>任务 #{job.id} · {len(documents)} 单</strong>
          <p>按商品编码及名称快照合并；拣货后仍需逐单配货、复核。</p></header>
          <div class="meta"><p>来源订单：{'；'.join(order_labels)}</p></div>
          <table><thead><tr><th style="width:7%">拣货</th><th style="width:10%">商品 ID</th><th>商品名称快照</th><th style="width:12%">汇总数量</th></tr></thead><tbody>{rows}</tbody></table>
          <div class="tail"><p>{len(merged)} 项商品快照 · 共 {sum(merged.values())} 件</p><p class="note">{e(notes_text)}</p>
          <p>拣货签字：________________　复核签字：________________</p></div>
          <footer>合并拣货任务 #{job.id} · 第 <span class="page-no"></span> / <span class="page-count"></span> 页<br>
          请求人 {e(job.requested_by)} · {e(business_time(job.requested_at))} · 来源文档摘要 {e(digest([doc.document_hash for doc in documents])[:16])}<br>
          仅记录打印请求，不能确认实际出纸</footer></template>"""]
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>配货单</title><style>
      @page { size:A4; margin:14mm } *{box-sizing:border-box} body{font:12px/1.5 Arial,'Microsoft YaHei',sans-serif;color:#111;margin:0}
      .sheet{break-before:page;position:relative;width:182mm;height:267mm;padding-bottom:22mm;margin:0 auto}.sheet:first-child{break-before:auto}
      header{border-bottom:2px solid #111;padding-bottom:7px}h1{font-size:23px;margin:0}p{margin:7px 0;overflow-wrap:anywhere}
      table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:11px}th,td{border:1px solid #aaa;text-align:left;padding:6px;overflow-wrap:anywhere}
      thead{display:table-header-group}tr{break-inside:avoid}footer{position:absolute;bottom:0;left:0;width:100%;font-size:9px;color:#555;border-top:1px solid #ccc;padding-top:6px}
      .note{white-space:pre-wrap}.toolbar{background:#eef5f1;padding:12px;margin-bottom:16px;text-align:center}
      @media print{.toolbar{display:none}}@media screen{.sheet{margin:18px auto 32px;outline:1px solid #ddd}}
      </style></head><body><div class="toolbar">配货文档快照。查看不登记请求；再次打印须填写补打原因，并重新核验订单状态。</div><main id="pages"></main>""" + ''.join(sources) + """
      <script>(()=>{
      // Paginate against measured A4 dimensions, including long CJK names and notes.
      // All business text is escaped server-side; this script only clones/textContent-writes it.
      const host = document.getElementById('pages');
      for (const source of document.querySelectorAll('.document-source')) {
        const fragment = source.content;
        const sheets = [];
        let sheet, content, table, tbody;
        function fresh(first = false) {
          sheet = document.createElement('section'); sheet.className = 'sheet';
          content = document.createElement('div'); content.className = 'content';
          content.append(fragment.querySelector('header').cloneNode(true));
          if (first) content.append(fragment.querySelector('.meta').cloneNode(true));
          sheet.append(content, fragment.querySelector('footer').cloneNode(true));
          host.append(sheet); sheets.push(sheet); table = null; tbody = null;
        }
        function fits() {
          return content.getBoundingClientRect().height <= sheet.clientHeight - parseFloat(getComputedStyle(sheet).paddingBottom) - 3;
        }
        function ensureTable() {
          if (table) return;
          table = document.createElement('table');
          table.append(fragment.querySelector('thead').cloneNode(true));
          tbody = document.createElement('tbody'); table.append(tbody); content.append(table);
        }
        fresh(true);
        for (const row of fragment.querySelectorAll('tbody tr')) {
          ensureTable(); const clone = row.cloneNode(true); tbody.append(clone);
          if (!fits()) {
            clone.remove(); if (!tbody.children.length) table.remove();
            fresh(); ensureTable(); tbody.append(clone);
          }
        }
        for (const block of fragment.querySelector('.tail').children) {
          if (!block.classList.contains('note')) {
            const clone = block.cloneNode(true); content.append(clone);
            if (!fits()) { clone.remove(); fresh(); content.append(clone); }
          } else {
            let paragraph = document.createElement('p'); paragraph.className = 'note'; content.append(paragraph);
            for (const char of block.textContent) {
              paragraph.textContent += char;
              if (!fits()) {
                paragraph.textContent = paragraph.textContent.slice(0, -char.length);
                fresh(); paragraph = document.createElement('p'); paragraph.className = 'note';
                paragraph.textContent = '配货备注（续）：' + char; content.append(paragraph);
              }
            }
          }
        }
        sheets.forEach((page, index) => {
          page.querySelector('.page-no').textContent = index + 1;
          page.querySelector('.page-count').textContent = sheets.length;
        });
      }
      document.documentElement.dataset.paginationReady = 'true';
      })();</script>""" + print_controls(api_base, job.id, documents, layout, document_id) + "</body></html>"
