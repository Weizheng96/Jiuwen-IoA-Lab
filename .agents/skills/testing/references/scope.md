# 范围与覆盖

push/PR 的 A 与 B 由 `tools/check_scope.py` 选择；`--changed-from <已提交基线>` 只比较基线到 HEAD，不包含工作区。选择结果只提供快速反馈，开发者仍按不变量补足测试。完整回归由 stage validation 的 full 或定期任务执行，安装部署使用 deployment 专项。

## 两个测试层

| 层 | 内容 | 入口 | 用途 |
|---|---|---|---|
| fast | 未列入 `tests/chain_layer.json` 的全部用例：纯规则、单组件、桩传输 | `check.py --layer fast` | 日常回归，整层数分钟 |
| chain | 清单所列用例：实测单例耗时达到清单阈值的整链路、真实进程、以及需要真实九问/模型的 opt-in 用例 | `check.py --layer chain`，或 `--full` 连同 fast 一起 | 阶段交付、发布与定期任务 |

清单只按函数名登记，参数化用例随函数归层；`tests/conftest.py` 在收集时给清单用例加 `chain` 标记。归层依据是真实 XML 逐项耗时，不凭估计：新增用例先留在 fast，整仓或 chain 运行后用 `python tools/test_layers.py --audit <pytest.xml>` 列出达到阈值而未登记、或已登记却实测很快的函数，再更新清单。清单里不存在的函数会让 fast 计划直接失败。

失败用 `--diagnose <模块名>` 跑整个文件定位，不自动重跑慢矩阵。合并或删除用例先在 `tools/test_layers.py` 记录独立规则、失败条件和原协议路径的新归属，不能把抽样或减少 nodeid 当作覆盖保持。C/D 仍执行其完整范围。

优化先读取 XML 逐项耗时及实际准备成本，不凭估计删慢项。长用例诊断栈仅帮助定位，不表示延期或通过；FAILED 后等待原任务 trace、XML 和退出码，未运行 selfcheck 单列。测试替身不能证明真实模型、原生确认、邮件或成片已验收。
