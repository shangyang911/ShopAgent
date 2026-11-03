"""Print views require an explicit recorded intent and a fresh order-state check."""
import json


def print_controls(api_base, job_id, documents, layout, document_id=None):
    config = json.dumps({'api': api_base, 'jobId': job_id, 'layout': layout, 'documentId': document_id,
                         'sources': {doc.order_id: doc.id for doc in documents}}, ensure_ascii=False).replace('<', '\\u003c')
    return '''<style>
      .print-warning{display:none}.toolbar button,.toolbar a{margin:4px 8px}.toolbar [role=alert]{color:#a11}
      @media print{body:not([data-print-approved=true]) #pages{display:none}
        body:not([data-print-approved=true]) .print-warning{display:block;font-size:18px}}
      </style><div class="print-warning">未登记新的打印请求或单据状态未核验。请返回文档页面，使用“登记补打并打印”。</div>
      <script>(()=>{const printConfig = ''' + config + ''';
      const statusNames={pending_payment:'待付款',paid_pending_shipment:'待发货',in_delivery:'配送中',completed:'已完成',cancelled:'已取消 · 单据失效',refunded:'已退款 · 单据失效'};
      const toolbar=document.querySelector('.toolbar');
      const errorLabel=document.createElement('p');errorLabel.setAttribute('role','alert');toolbar.append(errorLabel);
      const reprint=document.createElement('button');reprint.textContent='登记补打并打印';toolbar.append(reprint);
      const alternate=document.createElement('a');alternate.textContent=printConfig.layout==='summary'?'查看逐单配货单':'查看合并拣货汇总单';
      alternate.href=printConfig.api+'/print-jobs/'+printConfig.jobId+'/html?layout='+(printConfig.layout==='summary'?'orders':'summary')+(printConfig.documentId?'&document_id='+printConfig.documentId:'');toolbar.append(alternate);
      let used=false, validating=false, busy=false, retryKey=null, retryReason=null;
      async function checked(url, options) {
        const response=await fetch(url,{credentials:'include',...options});
        if(!response.ok){let message='请求失败 ('+response.status+')';try{const body=await response.json();if(typeof body.detail==='string')message=body.detail;}catch{}throw new Error(message);}
        return response;
      }
      window.addEventListener('afterprint',()=>{document.body.dataset.printApproved='false';});
      window.printRegisteredDocument=async()=>{
        if(used||validating)throw new Error('本次请求已打开打印对话框。再次打印请登记补打原因。');
        validating=true;
        try{
          const response=await checked(printConfig.api+'/print-jobs/'+printConfig.jobId+'/validate'+(printConfig.documentId?'?document_id='+printConfig.documentId:''));
          const current=await response.json();
          for(const order of current.orders)for(const node of document.querySelectorAll('[data-order-id="'+order.id+'"]'))node.textContent=statusNames[order.status]||order.status;
          used=true;document.body.dataset.printApproved='true';window.print();
        }catch(error){errorLabel.textContent=error.message;throw error;}
        finally{validating=false;document.body.dataset.printApproved='false';}
      };
      reprint.addEventListener('click',async()=>{
        if(busy)return;
        const reason=window.prompt('请输入补打原因（必填）。浏览器取消打印仍保留请求记录。');if(!reason||!reason.trim())return;
        if(reason.trim().length>1000){errorLabel.textContent='补打原因最多 1000 字';return;}
        busy=true;reprint.disabled=true;errorLabel.textContent='';
        try{
          if(retryReason!==reason.trim()){retryKey=crypto.randomUUID();retryReason=reason.trim();}
          const response=await checked(printConfig.api+'/print-jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_ids:Object.keys(printConfig.sources).map(Number),source_documents:printConfig.sources,reprint_reason:reason.trim(),layout:printConfig.layout,idempotency_key:retryKey})});
          const job=await response.json();
          const rendered=await checked(printConfig.api+'/print-jobs/'+job.id+'/html?layout='+printConfig.layout);
          const html=await rendered.text();document.open();document.write(html);document.close();
          await window.printRegisteredDocument();
        }catch(error){errorLabel.textContent=error.message;}
        finally{busy=false;reprint.disabled=false;}
      });
      })();</script>'''
