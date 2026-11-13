"""Public agent instructions and checksums for the two role-skill downloads and canonical unified-client instructions."""
import hashlib

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse


def router(platform):
    result = APIRouter()

    @result.get('/api/access/bundles')
    def bundles():
        items = []
        for role, name in (('assistant', 'ioalab-assistant-skill.zip'), ('service', 'ioalab-provider-skill.zip')):
            path = platform.s.bundles_dir / name
            item = {'role': role, 'name': name, 'url': platform.s.public_url.rstrip('/') + '/downloads/' + name, 'available': path.is_file()}
            if path.is_file():
                with path.open('rb') as source: checksum = hashlib.file_digest(source, 'sha256').hexdigest()
                item.update(size=path.stat().st_size, sha256=checksum)
            items.append(item)
        return {'schema': 'ioalab.access-bundles/1', 'bundles': items}

    @result.get('/readme_agent.md', response_class=PlainTextResponse)
    def readme():
        base = platform.s.public_url.rstrip('/')
        return f'''# 将用户自己的 Agent 接入 IoA Lab

平台地址：{base}

用户已要求接入时，按以下流程完成本机安装和接入；无需用户逐条指定工具。

1. 读取正式仓库的 README_AGENT.md：
   https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/develop/README_AGENT.md
   按其中的 GitCode 已发布 tag、固定 commit 和 bootstrap 流程安装同一个 ioalab-client。
   不把网站的角色 Skill ZIP 当作完整客户端安装包；角色 Skill 必须由已安装客户端导出。
   未发布的开发版本不可冒充已发布 tag；不能读取正式引导时报告真实原因并停止。
2. 按 bootstrap 回执导出并通过宿主原生入口导入两个轻量角色 Skill，保留已有宿主、模型、权限和数据。
   加载所选角色 Skill 后调用 account.setup，平台地址使用 {base}；由本机受控账号页录入账号或注册。
   密码和设备凭据不进入聊天或工具参数；状态通过 account.setup.status 查询。
3. 按角色 Skill 的公开宿主配置、runtime.start、runtime.status 流程完成启动。
   调用 runtime.mcp.configuration 获取同一客户端环境的本地 stdio 命令；通过宿主原生 MCP 设置登记。
   九问使用「扩展 → 我的扩展 → 连接器 → 创建 → 配置连接器」并填写回执 jiuwenJson。
   没有原生登记工具时给用户展示 JSON 和操作步骤，等待其完成；不猜测私有 API。
   已安装客户端可同时服务多个 MCP 宿主；WSS 常驻执行者按原配置和租约保持唯一。

可用流程：`registry.interfaces` 管理消息接口，`messaging.interaction` 私人交互，
`delegation.trade` 委托交易，`delegation.case` 原报价/合同争议，`services.management` 服务发布与履约。
通过 MCP `ioa_forms` / `ioa_open_form` 或公共 `runtime.forms.*` 操作访问同一流程。
支持 MCP Apps 的宿主内嵌显示；其他宿主使用客户端受控浏览器，不把 A2UI 等同于 MCP Apps。

同机同用户切换宿主可复用兼容 SDK 安装和同一配置路径。MCP 配置按宿主分别登记；
后台 AgentHost 能力必须独立验证。交接活跃执行者先正常排空旧实例，保留原身份、合同、会话和未知操作。
工具接入成功不证明新宿主具备无人值守的收信或生产能力。
'''

    return result
