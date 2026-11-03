# E-commerce project

Single-store product admin built with Vue 3 + TypeScript and a modular FastAPI API.

The shipping page now combines courier fulfilment with the existing AMap map and routes. See [courier phase-one workflow](docs/SHIPPING-phase1.md) for waybill registration, physical handover, inventory behavior, and historical backfill.

Phase two adds a Kuaidi100 tracking adapter, cached delivery history, auditable logistics follow-ups, dashboard links and an agent logistics tool. Credentials are not yet configured; see [phase-two setup and behavior](docs/SHIPPING-phase2.md).

Phase three adds electronic labels, batch label requests and printing, batch dispatch, optional multiple parcels per order, and auditable actual freight settlement/reporting. The existing primary-waybill API remains compatible; see [phase-three business rules and configuration](docs/SHIPPING-phase3.md). Real label issuance requires separately configured courier accounts.

The home page now opens the business overview. Product management is at `/products`, the inventory center at `/inventory`, and the order workbench at `/orders`. See [core module implementation notes](docs/IMPLEMENTATION-core-modules.md) for business rules, additive historical-data initialization, API behavior, and validation commands.

## Frontend

Agent 的固定任务集、离线回归、真实模型评测与报告使用方式见 [Agent 评测体系](docs/AGENT-EVALUATION.md)。

智能补货、待发货分析及核对后创建草稿的规则见 [运营 Copilot](docs/OPERATIONS-COPILOT.md)。

知识版本发布、生效过滤、引用原文及离线检索评测见 [RAG 证据链](docs/KNOWLEDGE-EVIDENCE.md)。

请求轨迹、用量成本、用户反馈和 SKU 商品工具口径见 [AI 运行面板](docs/AI-OBSERVABILITY.md)。

The notifications center, product batch actions, and customer history/follow-up panels are described in [operations enhancements](docs/OPERATIONS-ENHANCEMENTS.md), including module boundaries and transaction rules.

```powershell
cd frontend
npm run dev
```

Open <http://127.0.0.1:5173>.

## Backend

See [backend/README.md](backend/README.md) for environment setup and run commands.

The product page currently includes search, category/price/color/rating/shipping/status filters, sorting, and pagination. Product records are stored in the local MySQL database configured in `backend/.env`.

The frontend composes pages from `frontend/src/views`, keeps product UI and state in `frontend/src/features/products`, and shares language/UI capabilities from `frontend/src/shared`. The selected language persists in browser storage. The backend keeps product API and persistence code together in `backend/app/features/products`, with database and configuration infrastructure kept separate.
