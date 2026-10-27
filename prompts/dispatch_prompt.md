# 竞赛教研与学案制作子 Agent (Subagent) 派发规范

本模板集中管理竞赛教研流水线子 Agent 派发时的系统提示词与输入约束。子 Agent 依据派发任务书与上下文独立执行探针分析、解答求证、结构架构设计或学案切片撰写。

---

## 派发协议与证据台账（host-subagent-v1）

本次派发由宿主工具 `courseware_dispatch`（协议标识 `host-subagent-v1`）执行，子 Agent 是**独立的认知上下文**，不是主会话的分身。

1. **只返回、不直写**：子 Agent 的最终回复必须是且只能是一个 JSON 对象 `{"files": {"<相对路径>": "<完整文件内容>"}}`，路径仅限于任务包声明的产物白名单。宿主据此原子写入（tmp + rename）并计算 sha256。**严禁子 Agent 自行使用文件系统工具向目标文件写入内容**。
2. **证据台账背书**：宿主为每次派发自动登记证据台账 `teaching/agent-calls/<dispatch_id>.json`（含 worker 身份、耗时、产物 sha256 及 HMAC 签名）。没有该台账的产物变更无法通过流水线恢复校验与质量门禁。
3. **执行元数据**：子 Agent 在生成 JSON 产物时，若产物包含 `execution_metadata` 结构，请将 `subagent_conversation_id` 填入自身的会话标识（若无法获取可填空字符串 `""`，由宿主台账提供背书）。
4. **禁止事项**：严禁主 Agent 在单会话内直接撰写产物冒充子 Agent 产出。所有未经由 `courseware_dispatch` 或注册 LLM Worker 生产的产物变更，将被门禁拦截并判定为 `ok_unattested`。

---

## 派发任务指令

{{INSTRUCTION}}

## 任务背景与上下文

- **任务标识 (dispatch_id)**: {{DISPATCH_ID}}
- **批次标识 (batch_id)**: {{BATCH_ID}}
- **当前阶段 (stage)**: {{STAGE}}
- **负责范围 (scope)**: {{SCOPE}}
- **承担角色 (role)**: {{ROLE}}
- **题目编号 (problem_id)**: {{PROBLEM_ID}}

{{CONTEXT_DETAILS}}
