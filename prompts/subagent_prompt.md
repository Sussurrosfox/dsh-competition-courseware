# 竞赛教研专职子 Agent (Subagent) 派发规范

本模板集中管理子 Agent 派发时的系统提示词与输入约束。子 Agent 依据流水线阶段派发单起草认知产物（探针、题解、大纲架构、学生研学任务包或小问解答），必须独立运行且不得在主 Agent 会话内直写。

---

## 派发协议与证据台账（host-subagent-v1）

本次派发由宿主工具 `courseware_dispatch`（协议标识 `host-subagent-v1`）执行：

1. **认知隔离**：子 Agent 是独立的子上下文，不是主会话的分身。
2. **只返回、不直写**：子 Agent 严禁在本地磁盘直接创建或修改任何工作区文件！子 Agent 的回复必须是且只能是一个 JSON 代码块或 JSON 字符串：
   ```json
   {
     "files": {
       "<artifacts 中的相对路径>": "<完整文件内容>"
     },
     "execution_metadata": {
       "summary": "<针对本派发任务完成情况的一句话摘要>"
     }
   }
   ```
3. **白名单校验与原子写入**：宿主工具 `courseware_dispatch` 在收到回复后，对返回的文件路径与派发单 `artifacts` 白名单做严格核对；使用原子写机制（tmp + os.replace）安全落盘，并生成 HMAC 签名证据台账 `teaching/agent-calls/<dispatch_id>.json`。
4. **零信任门禁卡点**：所有未附带合规签名证据台账的产物变更，在状态机重推导与门禁检查中一律判定为 `ok_unattested` 并阻断交付（`check_agent_audit.py` 拒绝通过）。
5. **严禁主 Agent 代写**：严禁主 Agent 在自身单会话内直接撰写派发单 artifacts 声明的文件。若遇错误，必须通过返修流水线重新派发，严禁越权代劳。

---

## 任务包输入规范

派发单传递给子 Agent 的信息包括：
- `stage`: 生产阶段（如 T1, T2, RB, T2_repair 等）
- `scope`: 涉及模块范围（如 architect, probe, solver, student_task, review 等）
- `instructions`: 明确的教研任务指引与质量标准
- `context`: 题目题面、原材料片段、大纲方案或待返修审查意见
- `artifacts`: 必须交付的相对路径清单
