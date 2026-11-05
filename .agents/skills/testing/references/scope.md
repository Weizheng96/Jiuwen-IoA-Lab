# 范围与覆盖

push/PR 的 A 与 B 由 `tools/check_scope.py` 选择；`--changed-from <已提交基线>` 只比较基线到 HEAD，不包含工作区。选择结果只提供快速反馈，开发者仍按不变量补足测试。完整回归由 stage validation 的 full 或定期任务执行，安装部署使用 deployment 专项。

业务迭代可加 `--layer first`，失败用 `--diagnose <模块名>` 定位，不自动重跑慢矩阵。第一层必须保留有效覆盖；合并或删除用例先在 `tools/test_layers.py` 记录独立规则、失败条件和原协议路径的新归属，不能把抽样或减少 nodeid 当作覆盖保持。C/D 仍执行其完整范围。

优化先读取 XML 逐项耗时及实际准备成本，不凭估计删慢项。长用例诊断栈仅帮助定位，不表示延期或通过；FAILED 后等待原任务 trace、XML 和退出码，未运行 selfcheck 单列。测试替身不能证明真实模型、原生确认、邮件或成片已验收。
