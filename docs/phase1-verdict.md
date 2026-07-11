VALIDATED

# Phase 1 宿主 ABI Spike 结论

日期：2026-07-11

## 五项验收

1. **context-aware `/sgx start` 获得物理 session_id**：通过。
   - Gateway 使用当前持久会话 ID。
   - CLI 使用 `agent.session_id`。
   - TUI 使用 `session["session_key"]`，不使用 UI 临时 sid。
2. **插件 turn controller 返回 continuation**：通过。
   - CLI、Gateway、TUI 均调用同一 `invoke_turn_controllers()`。
   - sync/async handler 均有超时与异常隔离。
3. **Gateway 使用原生 FIFO 启动第二个真实回合**：通过。
   - continuation 生成 `MessageEvent`，经 `_enqueue_fifo()` 和现有 `_run_agent` 递归路径运行。
4. **真实用户消息优先**：通过。
   - Gateway pending slot/overflow 保持用户在自动 continuation 前。
   - CLI 发现 pending input 时不运行 controller。
   - TUI 先 `_drain_queued_prompt()`，再处理自动 continuation。
5. **compression 后触发 `on_session_rotate`**：通过。
   - hook 在 child session 创建并更新 system prompt 后调用。
   - `/sgx` 插件把 active binding 从旧 session 迁移到新 session。

## 关键不变量

- Legacy `handler(raw_args)` 保持兼容。
- Host ABI 名称不包含 `supergoal` / `sgx`。
- 每个物理回合最多一个自动 continuation：legacy goal 已入队时，plugin continuation 跳过。
- stale session 的 follow-up fail closed。
- `state_version` 按 `(session_id, plugin, controller)` 单调检查，CLI/Gateway/TUI 共享。
- `dedupe_key` 按 session 隔离。
- dedupe/state-version 跟踪表上限 4096，避免长期 Gateway 无界增长。
- controller 不改历史消息或 system prompt；continuation 走正常用户消息路径。

## 测试证据

- Phase 1 精确 seam：`24 passed`
- 插件契约与真实 directory-loader：`5 passed`
- 插件/TUI protocol/Gateway FIFO/compression/run-cleanup 相关回归：`231 passed, 1 deselected`
  - deselected 项是生产 checkout 同样失败的既有测试：`test_goal_hook_binds_session_context_inside_worker_thread`。
- 既有 Goal + replay 基线：`175 passed`
- Gateway/registry/model-tools/hardline 基线：`251 passed`
- `tests/tui_gateway/test_protocol.py`：`77 passed`
- changed-file `py_compile`：通过。
- 两仓 `git diff --check`：通过。
- async SessionStore 静态检查未增加违规；Phase 1 还顺手减少了 1 个既有 raw-store 调用。

## 已知边界

- TUI 命令上下文中的 `enqueue_followup` 仍 fail closed；TUI 自动续跑由 post-turn controller 的原生 `_run_prompt_submit()` 路径实现。
- 整个 `tests/test_tui_gateway_server.py` 在关闭项目默认并行参数、单进程运行时超过 600 秒；本次改动对应的真实 prompt-submit 测试单独通过。

## 下一阶段

允许进入 Phase 2：插件骨架与独立存储。生产 checkout 尚未切换，当前实现仅存在于隔离迁移 worktree。
