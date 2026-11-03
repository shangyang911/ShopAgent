# Agent 智能客服后端接口

当前实现面向现有电商管理后台的已登录管理员。前端在 `AgentView.vue` 中接入，侧栏机器人对话图标进入 `/agent`。
语音交互约定为微信式：按住开始录音，松开发送，上滑取消。后端不会在收到中间识别结果时提前调用 Agent。

## 启动与配置

在 `backend/` 执行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start-dev.ps1
```

在本地 `backend/.env` 添加百炼 Key，重启后端生效：

```dotenv
DASHSCOPE_API_KEY=你的百炼APIKey
DASHSCOPE_REGION=cn-beijing
AGENT_MODEL=qwen-plus
AGENT_ASR_MODEL=fun-asr-realtime
AGENT_TTS_MODEL=cosyvoice-v3-flash
AGENT_TTS_VOICE=longanyang
AGENT_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

也接受 `BAILIAN_API_KEY` 作为 Key 的兼容变量名。Key 只保留在后端，浏览器使用现有登录 Cookie。
默认使用北京地域公共接入点；新加坡使用 `DASHSCOPE_REGION=ap-southeast-1`，并使用对应地域的 Key。
如果控制台要求业务空间专属语音地址，填写 `DASHSCOPE_WORKSPACE_ID`，或显式配置 `DASHSCOPE_WS_URL`。
模型名、音色、地域与实际账号可用权限需要一致；服务状态接口只说明是否已配置，不代表已完成供应商连通验证。
`DASHSCOPE_BASE_URL` 可覆盖模型 HTTP 服务地址，`DASHSCOPE_WS_URL` 可覆盖语音 WebSocket 地址。

只有 Key 时可以启用模型和语音，不需要先建百炼应用。尚未配置门店知识库时，不提供确定的退换货政策。
后续建好知识库，填写 `DASHSCOPE_WORKSPACE_ID` 和 `BAILIAN_KNOWLEDGE_BASE_ID`；需要时使用 `BAILIAN_RAG_URL` 覆盖检索地址。
默认检索接入点为 `https://{WorkspaceId}.{地域}.maas.aliyuncs.com/api/v1/indices/rag/index/retrieve`。

应用启动时自动创建 `agent_conversations`、`agent_actions`、`agent_tickets` 和 `agent_conversation_archives` 表；归档使用独立表，无需修改已有会话表结构。
不替换原有业务表或数据；会话消息最多保留最近60条，模型上下文使用其中最近24条。

## HTTP：会话和人工工单

以下地址均以 `/api` 为前缀，携带现有的 HttpOnly `corelystics_session` Cookie。

- `GET /agent/status`：能力开关、音频格式和长度限制。未配置 Key 时返回能力关闭，应用仍可启动。
- `POST /agent/conversations`：无请求体，返回 `201 {"id":"会话UUID","created_at":"UTC时间"}`。
- `GET /agent/conversations/{id}`：返回 `messages`、`actions`、`tickets` 和 `busy`；只能访问自己的会话。
- `DELETE /agent/conversations/{id}`：归档并从最近列表移除自己的会话，返回204；重复删除仍返回204，处理中返回409。归档后不能再读取或通过WebSocket使用该会话；未确认动作失效，已有工单与订单审计保留。
- `GET /agent/tickets?page=1`：每页20条当前账号的人工工单摘要。
- `GET /agent/tickets/{id}`：工单摘要、状态及创建/更新工单时保存的对话快照 `transcript`。
- `PATCH /agent/tickets/{id}`：请求 `{"state":"in_progress"}` 或 `{"state":"resolved"}`；已解决工单不能重新打开。

人工协同目前是持久化工单，不是实时人工通话。工单会带上已有识别文字和历史回复。
取消、退款等实际业务事件仍可从现有订单操作历史中查看。

## WebSocket：连接和事件

先创建会话，再连接：

```text
ws://localhost:8001/api/agent/ws?conversation_id=<会话UUID>
```

HTTPS 部署使用 `wss://`。连接域名与登录 API 使用相同主机名，避免 `localhost` 和 `127.0.0.1` 混用导致 Cookie 丢失。
浏览器自动携带该主机的登录 Cookie，无须在 URL 中传令牌或百炼 Key。
未登录、会话不属于当前账号或 Origin 未获允许时，握手被拒绝，关闭码为1008。
部署域名通过 `AGENT_ALLOWED_ORIGINS` 配置，用逗号分隔；同源访问也可通过校验。

握手成功后的首条消息：

```json
{"type":"session.ready","conversation_id":"...","protocol_version":1,"capabilities":{"chat":true,"voice_input":true,"voice_output":true,"knowledge_base":false,"handoff":"ticket","identity":"administrator","business_tools":["search_orders","get_order","search_products","prepare_cancel_order"]}}
```

所有请求事件均发送 JSON。`request_id` 使用每轮新生成的 UUID 字符串，长度8–64，仅允许字母、数字、下划线和连字符。
服务端回复携带相同的 `request_id`。同一会话同时只允许一轮请求；跨连接同样受数据库租约保护。
每20–30秒发送 `{"type":"ping"}`，收到 `{"type":"pong"}`。120秒无收到消息则关闭连接。

### 文字提问

```json
{"type":"chat.send","request_id":"<UUID>","text":"查询待发货订单","speak":true}
```

`text` 最多2000字符，`speak` 默认为 `true`。后端会返回：

1. `user.message`：确认收到的用户文字，语音提问时也返回最终识别文字。
2. `state`：`thinking` / `processing`，处理工具时附带 `tool`。前端可映射成友好状态文案。
3. `assistant.message`：`message.text` 为详细回复，`message.speech_text` 为短语音摘要，`message.sources` 为实际检索资料的标识和标题。
4. 如果需要确认、生成工单或执行了操作，附加对应事件。
5. 如果启用播报，返回音频事件和二进制帧。
6. `turn.done`：本轮结束，`status` 为 `completed` / `failed` / `cancelled`。

文本按完整消息返回；音频、识别结果和处理状态是流式返回。

### 按住录音

按下按钮后先发送：

```json
{"type":"audio.start","request_id":"<UUID>","format":"pcm_s16le","sample_rate":16000,"channels":1,"speak":true}
```

等待 `audio.ready` 后，将音频以 WebSocket **二进制帧**连续上传，建议每40–100ms一帧。
仅支持16 kHz、单声道、16-bit signed little-endian原始 PCM；每帧不超过64 KiB且字节数为偶数。
浏览器 `MediaRecorder` 常见输出为 WebM/Opus，不能将这类压缩数据直接当作 PCM 上传。前端需采集并转换/重采样。
单次最长60秒；提交时少于0.3秒音频会被拒绝。

录制期间收到 `transcript`，其中 `text` 是当前累计识别文字，`sentence_final` 仅表示识别句子是否稳定，不表示已提交本轮。
`audio.ready` 表示后端已接受录音，供应商服务若连接失败，会随后返回错误；前端据此停止采集。

松开按钮，确保最后一帧上传完毕后，发送：

```json
{"type":"audio.commit","request_id":"<与audio.start相同的UUID>"}
```

收到 `audio.committed` 后，后端等待最终识别结果，再检索知识/调用工具和生成回复。
上滑取消、失去按压或离开页面时，停止本地麦克风和上传，发送：

```json
{"type":"turn.cancel","request_id":"<当前轮UUID>"}
```

被取消且尚未提交的录音不会进入 Agent，也不会写入用户提问历史。
前端需要同时清空当前轮播放队列；收到后续旧轮音频/事件时按 `request_id` 丢弃。
上一轮只剩语音播报时，直接发送新一轮 `audio.start`、`chat.send` 或 `action.confirm` 也会自动中断旧播报。
已保存并展示的确认卡片会保留；因此用户可在听到确认提示时按住说“确认”，无需等待播报结束。
若用户取消的是尚未发布的请求，则该请求生成但未发布的待确认动作也会失效。

### 语音播放

详细文字先通过 `assistant.message` 返回。后端只把 `speech_text` 送去合成：

1. `audio.start`：包含本轮 `request_id`、`format:"pcm_s16le"`、`sample_rate:24000`、`channels:1`。
2. 后续二进制帧是该轮原始 PCM 音频，无 WAV 文件头。前端通过 Web Audio 解码、排队播放；注意跨帧保留不完整的采样字节。
3. `audio.end`：`completed:true` 表示合成结束；前端本地队列可能仍在播放。

服务端与客户端的 `audio.start` 是两个方向的事件，输入16 kHz、输出24 kHz。
音频帧归属于最近一个服务端 `audio.start`，直到 `audio.end`；每连接最多一轮，因此不混流。
语音合成失败返回 `warning`，文字答案仍有效，本轮仍可为 `completed`。
浏览器播放应由点击客服图标、按住按钮等用户手势解锁 AudioContext。

### 订单操作确认

Agent 目前接入订单/商品查询和取消订单，取消必须提供明确订单与原因。
查询使用数据库真实内容；现有系统没有快递轨迹、预计送达时间或支付渠道退款接口，不承诺这些结果。
取消业务复用原有 `OrderCRUD.update_status`，保留原有状态校验、预留库存释放和订单事件记录。

需要取消订单时，收到 `action.required`，以及 `assistant.message.message.action`：

```json
{"type":"action.required","request_id":"...","action":{"id":"<动作UUID>","kind":"cancel_order","state":"pending","order_id":1,"order_number":"ORD-...","total":"20.00","reason":"买错了","effect":"取消订单并释放预留库存，不会发起资金退款。","title":"取消订单 ORD-...","expires_at":"...Z","result":null}}
```

前端展示订单号、金额、取消原因、影响及确认/撤销按钮。用户点击确认：

```json
{"type":"action.confirm","request_id":"<新UUID>","action_id":"<动作UUID>","speak":true}
```

也可用文字或语音准确回复“确认”“确定”“确认取消”“确认办理”“是的确认”，后端仅对当前会话的有效待确认动作执行。
引用或包含“确认”的长句不会被当作确认口令。撤销按钮发送：

```json
{"type":"action.decline","request_id":"<新UUID>","speak":false}
```

确认有效期5分钟。新的普通问题会使旧待确认动作失效。确认时再次检查登录态、动作归属、订单状态、金额和更新时间。
成功返回 `action.result`，同一动作的重复确认返回既有结果，库存只释放一次。
取消动作记录与订单状态/库存/审计写入同一事务，失败一起回滚。
已经进入执行的确认不能通过取消录音事件撤销；界面应等待结果。取消订单本身不发起资金退款。

### 人工工单

用户点击转人工按钮时，可直接发送，无需调用模型：

```json
{"type":"handoff.request","request_id":"<新UUID>","summary":"需要人工核实退货政策","speak":false}
```

收到 `ticket.created`，其中 `ticket` 包含工单ID、摘要、会话ID和 `pending` 状态。
同一会话已有待处理工单时复用并更新对话快照，不重复创建。
没有配置百炼 Key 时，仍可以创建会话、查看记录和提交人工工单。

### 错误、重连与重试

错误统一为 `{"type":"error","request_id":"...","code":"...","message":"..."}`。
校验失败时可能没有 `request_id`；请求未被接受时仅返回 `error`，不会发送 `turn.done`。
已接受的轮次失败则返回 `error` 后发送 `turn.done`。
主要错误包括 `service_unconfigured`、`unauthorized`、`busy`、`invalid_event`、`audio_state`、`audio_too_short`、`audio_too_long`、`audio_backpressure`、`action_expired`、`action_inactive`、`order_changed`、`provider_auth` 和 `provider_busy`。

断线后先通过 HTTP 获取原会话记录，再重新连接同一个会话ID。`actions` 数组是当前动作状态的依据，消息内附带的是当时的快照。
动作可能为 `pending`、`completed`、`declined`、`expired`、`superseded`、`cancelled` 或 `stale`。
已执行订单操作的结果保存在动作记录和订单历史中，即使浏览器没有收到结果也可以查询。
最近60条消息内，同一 `request_id`、相同提问的已完成回答会直接重放；已中断请求需核实状态后使用新的请求编号。
服务进程异常退出时，会话忙碌租约最多6分钟后释放。断线录音需要重新录制，不能自动重传确认指令或重放旧音频。

## 验证

客服测试使用隔离 SQLite 和百炼替身，不连接现有 MySQL，也不调用付费模型：

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_agent.py -v
```

供应商实连需要在本地 `.env` 配置有效 Key，并核实地域、模型权限、余额/额度和必要的业务空间ID。

协议依据：[百炼 WebSocket 概览](https://help.aliyun.com/zh/model-studio/realtime-websocket-overview)、[实时语音识别](https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide)、[CosyVoice WebSocket](https://help.aliyun.com/zh/model-studio/cosyvoice-websocket-api)、[Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)、[知识库检索](https://help.aliyun.com/en/model-studio/rag/quickstart)。
