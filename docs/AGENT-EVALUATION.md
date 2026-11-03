# Agent 评测体系

评测运行真实 `AgentEngine`、`AgentStore` 和订单/库存业务服务。每条任务单独创建内存 SQLite，数据是固定的合成订单，不读取或修改业务数据库。模型外部调用只在显式指定 `--mode live` 时发生。

## 运行

在 `backend` 目录执行：

```powershell
.venv\Scripts\python.exe -m evals.runner --mode offline
.venv\Scripts\python.exe -m unittest discover -s tests -p test_agent_evals.py -v
```

离线回放使用预先写好的工具选择与回答，作用是验证引擎集成、业务契约、评分和报告。**离线通过率不是模型准确率，也不能作为简历中的模型效果指标。**

真实模型模式使用 `backend/.env` 中已有的百炼配置，会产生模型请求和供应商费用：

```powershell
# 先选单个任务检查模型连通性
.venv\Scripts\python.exe -m evals.runner --mode live --case detail-01
# 完整模型评测
.venv\Scripts\python.exe -m evals.runner --mode live
```

两个模式都使用固定知识库夹具，不调用生产知识库。知识片段是否被正确引用可以测量；真实检索召回率不能由此得到。ASR/TTS 不在这批文字任务范围内。

## 任务与评分

`backend/evals/cases.py` 的 v3 定义 16 个场景，每个 5 种表述，共 80 条任务。在原有订单、商品、物流、知识、确认、安全和运营场景之上，增加SKU规格筛选与无匹配场景。相同场景的改写不代表80个独立业务场景；数据集升级后应重新建立基线，不与旧版直接比较通过率。

评分同时检查：

- 必须调用的工具以及禁止调用的工具。
- 关键参数，例如订单 ID、待发货状态、取消原因。
- 工具是否成功执行。
- 答案中的预设事实片段、禁止的虚假承诺、引用来源 ID。
- 真实动作预览/工单是否存在，且是否符合任务要求。
- 未经确认时订单状态、商品库存、SKU 可售与预留库存是否保持不变。

答案断言属于规则检查，不能代替人工或语义忠实度评测。危险操作检查失败始终返回非零退出码；其他断言可用 `--min-pass-rate` 配置门槛（默认 1）。现有 `test_agent.py` 继续验证独立确认、动作过期、身份与并发边界。

## 报告与基线

默认输出至项目 `.local-test/agent-evals/`，每次生成唯一的 JSON、Markdown、HTML 文件。HTML 可以直接在浏览器打开，展开查看逐任务结果、失败断言、工具参数和事件轨迹。文件仅包含合成任务数据，不保存 API Key、Cookie、系统提示词或供应商错误正文。

报告记录数据集版本/哈希、提示词哈希、工具定义哈希、模型名、任务结果、分类结果、总耗时 P50/P95、模型/工具调用耗时和供应商 token 用量。未返回 token 或未配置价格时为 `null`，不使用字符数伪造 token 或成本。回放模式的耗时不代表真实模型速度。

```powershell
.venv\Scripts\python.exe -m evals.runner --mode offline --baseline ..\.local-test\agent-evals\某次报告.json
```

基线必须具有相同模式和相同任务集哈希，可以对比不同提示词/工具版本。差值为本次减基线；提示词优化应基于真实模型模式中的失败样例进行，不能以脚本回放的成功作为模型提升证据。报告默认含合成取消原因和工单摘要，仅适用于本地评测，不应把真实客户信息加入夹具。
