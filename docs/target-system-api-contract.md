# 目标题目系统 API 契约（脱敏盘点）

> 本文档仅用于后续重写适配器。未包含账号、密码、Token、Cookie、个人信息、题目正文或抓包原文；当前实现不会调用保存或提交接口。

## 公共约定

- 方法：历史记录中所有业务接口均为 `POST`。
- 认证：除登录外，历史客户端使用 `Authorization: <TOKEN>`；抓包只证明存在该头，不证明 Token 前缀格式。
- JSON 响应信封：历史客户端按 `{code: integer, message?: string, msg?: string, data: any}` 读取。业务成功码被实现为 `0` 或 `200`。
- 2026-07-13 可见浏览器校准再次观测到登录、学校、题目列表、题目详情、抢单和 OCR 均返回 `200`；题目列表额外出现 `paper_status_dev` 请求字段。
- 验证等级：`抓包观测` 表示路径、方法、请求字段在脱敏抓包结构中出现；`代码推测` 表示只在历史客户端出现，未在抓包中出现。
- 可空性：抓包无法证明的字段均标为“未知”，不得据此生成强校验客户端。

## 端点

| 业务 | 路径 | 请求字段（类型） | 已知响应 `data` 字段 | HTTP | 等级 |
|---|---|---|---|---|---|
| 登录 | `/admin/login` | `username:string`, `password:string` | `token:string`, `info.developer_id:integer`, `info.nickname:string` | 200 | 抓包观测；响应字段来自历史客户端 |
| 用户信息 | `/admin/info` | 无 | 未捕获响应正文 | 200 | 抓包观测 |
| 学校 | `/admin/school/list` | `is_business:integer`, `schoolIds:array` | `list[].school_id`, `list[].school_name`（客户端使用） | 200 | 抓包观测 |
| 学院 | `/admin/college/list` | `school_id:integer` | 未捕获响应正文 | 200 | 抓包观测 |
| 年级 | `/admin/grade/list` | 无 | 未捕获响应正文 | 200 | 抓包观测 |
| 可接任务 | `/admin/research/aiTopicList` | `school_id:integer`, `subject_ids:array`, `status:integer`, `contain_img:integer`, `developer_id:integer`, `judge_admin_id:string`, `page:integer`, `pagesize:integer`, `check_developer_id:integer`, `check_time:integer`, `check_unlock:integer`, `is_unlock:integer`, `selfCate:integer`, `submit_time:integer`, `paper_status_dev:integer` | `list:array`（客户端使用；元素字段未知） | 200 | 抓包观测 |
| 题目详情 | `/admin/research/aiTopicInfo` | `id:integer` | 题目对象，字段未捕获 | 200 | 抓包观测 |
| 抢单 | `/admin/research/startDevAiTopic` | `id:integer` | 未捕获响应正文 | 200 | 抓包观测 |
| 取消抢单 | `/admin/research/cancelDevAiTopic` | `id:integer` | 未捕获响应正文 | 未观测 | **代码推测** |
| OCR | `/admin/openai/imgToText` | `multipart file:binary` 或历史客户端的 URL 上传形式 | `text:string` | 200 | 抓包观测；请求体字段未由抓包记录 |
| 保存答案 | `/admin/research/saveAiTopic` | `id:integer`, `topic:string`, `topic_text:string`, `answer:string`, `answer_text:string`, `topic_right:string`, `exam_point:string`, `status:integer`, `is_unlock:integer`, `isDevSubmit:integer` | 未捕获响应正文 | 200 | 抓包观测 |
| 我的勘误 | `/admin/goods/myErrataByDev` | `is_unlock:integer` | 未捕获响应正文 | 200 | 抓包观测 |
| 我的任务 | `/admin/task/user` | `is_settle:integer`, `is_unlock:integer`, `status:integer` | 未捕获响应正文 | 200 | 抓包观测 |
| 任务首页 | `/admin/task/index` | 无 | 未捕获响应正文 | 200 | 抓包观测 |
| 提现记录 | `/admin/developer/withdrawLog` | `developer_id:integer`, `type:integer`, `types:array` | 未捕获响应正文 | 200 | 抓包观测 |

## 状态字段

- `saveAiTopic.status=6`、`isDevSubmit=1` 仅来自旧客户端的“提交”路径，属于代码推测，并非抓包验证的枚举定义。
- 未来适配器的“保存草稿”必须先以只读/测试账号验证对应状态值；在验证前不得复用 `6/1`，也不得自动提交。
- 列表筛选中的 `status=0` 被旧客户端命名为“待接任务”，同样属于客户端语义，不是服务端正式文档。

## 证据与安全边界

- 抓包证据：`AUTOTEST` 分支的 `backend/captured_traffic/*.jsonl`，只读取结构，原文件不复制到当前分支。
- 代码证据：`AUTOTEST:backend/app/automation/api_client.py`。
- 机器可读版本见 `target-system-api-contract.schema.json`。
- 校准页面已定位：`button` 文本“识别录入”、文件入口“粘贴答案图片”、文本“确认提交答案”、文本“放弃作答”。选择器只用于可见浏览器填入；提交按钮永不由自动化点击。
- 本阶段不恢复旧控制台，不执行保存或提交请求。
