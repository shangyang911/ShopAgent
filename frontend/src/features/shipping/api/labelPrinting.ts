export interface PrintableLabel {
  order_number: string
  package_no: number
  tracking_number: string
  label_url: string
}

export function populateLabelPrintWindow(popup: Window, labels: PrintableLabel[]) {
  // 使用已取得的面单链接生成打印页，不在打印动作中再次向平台申请运单号。
  popup.opener = null
  const doc = popup.document
  doc.title = '电子面单批量打印'
  const style = doc.createElement('style')
  style.textContent = `body{font:14px sans-serif;margin:20px;background:#f3f5f5}header{margin:0 0 20px}article{width:100mm;background:white;margin:12px auto;break-after:page}img{display:block;width:100%;height:auto}p{overflow-wrap:anywhere}button{padding:10px 20px} @media print{@page{size:100mm 150mm;margin:0}body{margin:0;background:white}header,.label-caption{display:none}article{margin:0;width:100mm;break-after:page}article:last-child{break-after:auto}img{max-height:150mm;object-fit:contain}}`
  doc.head.append(style)
  const header = doc.createElement('header')
  const status = doc.createElement('p')
  status.textContent = `正在加载 ${labels.length} 张电子面单，请核对后打印。`
  const button = doc.createElement('button')
  button.textContent = '打印全部面单'
  button.disabled = true
  button.onclick = () => popup.print()
  header.append(status, button)
  doc.body.replaceChildren(header)
  let loaded = 0
  // 必须所有面单图片都加载成功才开放批量打印，避免漏印却误以为整批已经输出。
  for (const label of labels) {
    const article = doc.createElement('article')
    const caption = doc.createElement('p')
    caption.className = 'label-caption'
    caption.textContent = `${label.order_number} · 包裹 ${label.package_no} · ${label.tracking_number}`
    const original = doc.createElement('a')
    original.textContent = '打开平台原始面单'
    original.href = label.label_url
    original.target = '_blank'
    original.rel = 'noopener noreferrer'
    caption.append(' · ', original)
    const img = doc.createElement('img')
    img.alt = `${label.order_number} 包裹 ${label.package_no} 电子面单`
    img.onload = () => {
      loaded++
      if (loaded === labels.length) {
        status.textContent = `已加载全部 ${labels.length} 张面单；请选择 100 × 150 mm 纸张，核对打印机设置后打印。`
        button.disabled = false
      }
    }
    img.onerror = () => {
      status.textContent =
        '有面单尚未生成、链接已过期或平台返回 PDF。请刷新此页或打开平台原始面单核对；全部图片加载前不能批量打印。'
    }
    img.src = label.label_url
    article.append(caption, img)
    doc.body.append(article)
  }
}
