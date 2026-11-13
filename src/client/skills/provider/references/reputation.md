# 结清与信誉

只在真实交付、验收与应付款凭证齐全后结束合同。常驻 SDK 复用原 A2C Provider.close 提交结清卡，平台核真实付款、登记信誉并释放接单容量；不是模型自报评分或累计次数。

使用 provider.flow（action=reputation）（profile）读取并核验平台签名信誉；使用 provider.flow（action=status）（profile）查看所属服务和实际状态，必要时在平台服务方工作台查看原合同记录。没有原结清/信誉回执就保留未完成或结果待核状态，不因模型结束、消息 TTL、服务重启而释放名额。

用户询问信誉提交时，说明SDK依据真实凭证自动提交原结清卡；用provider.flow（action=tasks，原taskRef放input）及provider.flow（action=reputation）核实际状态，不再创建第二次提交。未知结清保留原合同和回执，不能自写记录、调用不存在的submit操作或经私有HTTP绕过SDK；恢复不新增信誉次数。
