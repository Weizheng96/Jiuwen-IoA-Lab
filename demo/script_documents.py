"""Generate a reviewable screenplay for each demo from its authored and published sources."""

import argparse
import hashlib
from pathlib import Path

from artifacts import inside, read_json, require

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
SCENES = {
    "global": (
        "全局视角",
        "global_story.py",
        "平台组件居中，左侧用户与个人助手一一配对，服务位于右侧；真实监控不显示讲解用户。",
    ),
    "delegation": (
        "美甲委托",
        "case_story.py",
        "小美店主、用户自己的九问、原剧本七家专业服务、平台组件与服务方账号。",
    ),
    "messaging": (
        "助手交互",
        "case_story.py",
        "王经理的个人助手（wang.manager）、赵专员的个人助手（demo_b@huawei.com）；独立收件客户端、通知邮箱与Outlook。",
    ),
}

# These are filming instructions, not a second source for captions or narration.
ACTIONS = {
    "global": {
        "intro": "完整片头，展示平台名称、英文名和一句话介绍。",
        "join": "三列配对网格，用户位于对应个人助手上方；助手随机错峰变彩色，部分并发，只播放单次接入路径。",
        "credit": "高亮支付中心，100虚拟币沿路径送达个人助手。",
        "provider": "服务方从灰色变彩色，展示登记与模板公示。",
        "needs": "多位用户提出不同需求，高亮用户及各自个人助手。",
        "authorization": "展示预算和委托类别，再播放用户授权路径；明确机制示意。",
        "discovery": "多个个人助手只向A2X注册中心发出发现请求。",
        "methods": "点击注册中心，展开分类树与ANN两种方法；实际使用分类树。",
        "compare": "高亮合同模板，横向比较交付件、报价和判据。",
        "contract": "高亮合同与签名，展示先约标准再成约。",
        "delivery": "服务方交付，路径进入存证与签名组件。",
        "acceptance": "路径返回个人助手本地，展示SDK沙箱验收。",
        "arbitration": "高亮仲裁，说明复算及文书；明确本轮未发生裁定。",
        "payment": "高亮模拟支付，验收和授权作为付款依据。",
        "reputation": "高亮信誉日志，显示委托结清后的记录关系。",
        "messages": "仅显示消息中心与个人助手间P2P路径。",
        "meeting": "展示结构化预约消息与离线保留，不画成平台代办日历。",
        "value": "用户淡出，36个助手及整列组件、服务区对齐本轮真实几何；2.8秒单层交叉淡化，首帧保持后继续播放，右侧真实行为日志渐显。",
        "internal": "全屏真实管理员监控中，委托与个人助手消息持续交错、并发出现，说明内部验证价值。",
        "external": "保持两类业务同时发生的全屏真实监控，说明开发者、用户与数据反馈价值；旁白后延续混合网络活动。",
    },
    "delegation": {
        "cover": "完整片头：美甲预约·合同委托。",
        "account": "真实平台注册表单：输入xiaomei@outlook.com、演示密码123456，点击注册，展示进入本人工作台。账号只在本轮隔离平台创建，不注册外部邮箱。",
        "configuration": "真实本地skill配置文件：从示例复制settings.local.json，填写本轮真实平台地址、xiaomei@outlook.com和123456，点击保存并重新读取核对。仅演示凭据允许显示，不展示模型或通知邮箱密钥。",
        "skill": "九问原生菜单选择IoA个人助手技能，保留可见选中状态；此时不发送隐藏技术提示。",
        "need": "完成前述注册、配置与原生技能选择后，只输入业务需求；右侧点亮个人助手组件，预算500虚拟币。预选步骤明确记录，不冒充无配置自动发现。",
        "discovery": "点击A2X组件，展示真实搜索和完整验签模板。",
        "authorization": "九问先请求发起委托授权，店主通过原生确认窗口或聊天回复确认后authorize保存范围；请求中不夹带技能路径或命令。",
        "quotes": "七家逐步筛选，灵犀239、快搭259、码上飞299三份真实签名提案；不补造历史信誉。",
        "choice": "九问比较验收器并推荐码上飞，店主只确认最终合同，不另选基础/高级版本。",
        "registration": "标准stepRequest出现后九问请求实名注册授权；用户同意后才继续生产，画面标明不提交真实实名资料。",
        "contract": "店主明确确认后采纳、签字、开工；高亮委托中心。",
        "production": "高亮服务方：独立进程按本单需求启动Swarm，不用样件代替。",
        "delivery": "高亮委托中心存证，九问等待签5后通过SDK取件。",
        "verification": "点击个人助手节点，展示本地WASM报告；暂不接受、不付尾款。",
        "payment": "店主第二次确认后签7并付款；点击模拟支付，同一合同已付299。",
        "provider": "整屏切到ISP_008，展示A2X分类树、同一摘要的完成记录和299实际收入。",
        "tool": "整屏运行真实交付配置：登记预约、查看名单、点击取消并确认已取消。",
        "close": "回到本人工作台，保持本单聚焦，回顾已核验流程。",
    },
    "messaging": {
        "cover": "完整片头：个人助手·留言与预约。",
        "policy": "切到王经理账号展示交互接口和精确白名单；屏外验收：非允许账号接口为空、发送403，结果保存在证据摘要。",
        "schedule": "保持王经理的真实工作台：上方网络固定可见，下方展示预约接口、inputSchema与允许账号。预约依据保存在本轮证据中，不另造日历画面。",
        "lookup": "赵专员的真实九问查询王经理对其开放的inputSchema；右侧上方保持网络，下方展示接口表单，正向消息由九问发送。",
        "forward": "切入王经理真实九问通知会话，显示赵专员的留言；右侧显示投递与处理回执。",
        "busy": "回到赵专员，展开11:00预约的真实冲突回执；不自动修改安排。",
        "reschedule": "赵专员明确授权改约14:00；高亮消息中心，王经理的接收客户端按规则再次检查。",
        "invitation": "展示真实邀请提交回执；旁白收尾版叠加“提交邀请→本人接受→进入日历”的流程说明，不声称本轮已送达、已接受或已入历。",
        "outlook": "真实Outlook打开本轮邀请，收件人接受，再在日历查看对应会议；预览尚无此镜头。",
        "close": "返回消息中心与本人工作台，回顾交互接口、范围、离线收取和处理回执。",
    },
}


def shots(case):
    if case == "global":
        from global_story import SHOTS

        return SHOTS
    from case_story import STORIES

    return [
        {"id": key, "text": text, "voice": voice} for key, text, voice in STORIES[case]
    ]


def published(case):
    if case == "global":
        proof = read_json(DEMO / "verification/global.json")
        return proof, inside(ROOT, proof["video"])
    proof = read_json(DEMO / "verification/cases.json")["cases"][case]
    return proof, inside(DEMO, proof["movie"])


def timestamp(seconds):
    minutes, centiseconds = divmod(round(float(seconds) * 100), 6000)
    return f"{minutes:02}:{centiseconds / 100:05.2f}"


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render(case, timing=None):
    title, source, roles = SCENES[case]
    scene = shots(case)
    require(
        set(ACTIONS[case]) == {s["id"] for s in scene},
        "Filming instructions must cover every shot",
    )
    proof, movie = published(case)
    voice_path = Path(timing) if timing else DEMO / "assets/voice" / case / "voice.json"
    voices = read_json(voice_path)
    by_id = {v["id"]: v for v in voices}
    synchronized=all(v['id'] in by_id and by_id[v['id']]['text']==v['text'] and by_id[v['id']]['voice']==v['voice'] for v in scene if not (case=='messaging' and proof.get('acceptancePresentation')=='narrated' and v['id']=='outlook'))
    preview = proof.get("preview", False)
    narrated=case=='messaging' and proof.get('acceptancePresentation')=='narrated'
    if narrated:scene=[s for s in scene if s['id']!='outlook']
    lines = [
        f"# {title}剧本",
        "",
        "由`demo/script_documents.py`生成，不手改。字幕和完整旁白来自场景源，片中时间来自配音时间轴。",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        f"| 场景源 | [{source}](../{source}) |",
        f"| 当前视频 | [{movie.name}](../out/{movie.name}) |",
        f"| 发布状态 | {'正式平台演示；Outlook接受入历为旁白说明' if narrated else ('预览；Outlook接受与入历待补录' if preview else ('正式成片' if synchronized else '剧本已更新，现有成片待重录'))} |",
        f"| 当前时长 | {proof['qa']['duration']:.2f}秒 |",
        f"| 角色与视角 | {roles} |",
        "| 声画要求 | 完整片头；52px高对比短字幕；按实测音长剪辑；A2C念A to C |",
        "| 证据入口 | [验证记录](../verification/README.md)；原始口令、密钥和私人消息不进入剧本 |",
        f"| 时间轴 | {'显式指定的工作配音；不代表已替换发布视频' if timing else '当前发布视频的实际时间轴'} |",
        "",
        "## 分镜",
        "",
        "| 镜头 | 当前片中时间 | 画面与操作 | 短字幕 | 完整旁白 | 证据与状态 |",
        "|---|---|---|---|---|---|",
    ]
    for shot in scene:
        clip = by_id.get(shot["id"])
        matches = (
            clip and clip["text"] == shot["text"] and clip["voice"] == shot["voice"]
        )
        if matches:
            position = voices.index(clip)
            end = (
                voices[position + 1]["at"]
                if position + 1 < len(voices)
                else (
                    clip["at"] + clip["duration"] + 0.55
                    if timing
                    else proof["qa"]["duration"]
                    - (
                        proof.get("monitor", {}).get("tailSeconds", 0)
                        if case == "global"
                        else 0
                    )
                )
            )
            interval = f"{timestamp(clip['at'])}—{timestamp(end)}"
            if timing:
                status = "工作配音时间；待正式编排发布"
            elif case == "global":
                status = (
                    "已录；有限随机讲解动画"
                    if shot["stage"] < 9
                    else "已录；单一全屏真实管理员监控"
                )
            else:
                status = "已录；实际执行与回执"
                if narrated and shot['id']=='invitation':status='邀请提交为实际回执；本人接受入历为标注的旁白说明'
                if case == "messaging" and shot["id"] in proof.get(
                    "interfaceRetake", {}
                ).get("shots", []):
                    status = "已录；真实接口配置复看，未重发消息"
        else:
            interval = "待补录" if not clip else "待重新配音/录制"
            status = (
                "计划镜头；不在当前预览中"
                if shot["id"] == "outlook"
                else "场景源与当前视频尚未同步"
            )
        lines.append(
            "| "
            + " | ".join(
                cell(v)
                for v in (
                    shot["id"],
                    interval,
                    ACTIONS[case][shot["id"]],
                    shot["text"],
                    shot["voice"],
                    status,
                )
            )
            + " |"
        )
    if case == "global" and not timing and proof.get("monitor", {}).get("tailSeconds"):
        end = proof["qa"]["duration"]
        start = end - proof["monitor"]["tailSeconds"]
        lines.append(
            f"| monitor-tail | {timestamp(start)}—{timestamp(end)} | 真实管理员监控保持全屏，继续展示网络；播放页随后循环监控录屏 | 无字幕 | 无旁白 | 实际录屏；回放标识持续显示 |"
        )
    lines += ["", "## 验收边界", ""]
    if case == "global":
        run = proof["experiment"]
        lines += [
            f"0—8只播放旁白对应路径，9—10用户虚化淡出、助手位置插值并渐变切入单一全屏真实管理员监控，旁白后继续展示网络；委托和P2P按可见传输活动接近配置，组件和短连接复用真实界面。接入、上下线、授权与条件仲裁为机制示意；本轮执行A2X分类树，ANN仅介绍方案。实际数据为{run['participants']}个独立客户端、{run['orders']}笔委托、{run['messages']}条消息。样例服务与虚拟币持续标识。"
        ]
    elif case == "delegation":
        lines += [
            "三份真实提案为灵犀239、快搭259、码上飞299虚拟币，最终采纳码上飞。用户先注册演示平台账号xiaomei@outlook.com（密码123456）、保存本地skill配置并从原生菜单选择技能，然后只发需求，并在委托授权、合同确认、实名环节、验收四处确认。这四次确认属于本场景，不是通用流程固定次数。中选摘要、完成摘要和实际付款一致；原版旧影片如尚未重录则镜头标待更新。当前不宣称已完成微信真实注册、审核或上线。"
        ]
    else:
        lines += [
            "本版本按用户选择用旁白说明后续接受入历，不编排Outlook操作镜头。invite_submitted、异步发件和待接受状态保留，说明图不作为本轮真实接受或日历证据。" if narrated else
            "白名单仅对指定平台账号授权，邮箱形式不代表企业邮箱认证，接口不公示到服务市场。冲突依据助手本地可编辑日程；邀请提交、邮件送达、用户接受与入历分别核验，不读取私人Outlook。当前预览跳过outlook镜头，后续补录会重排收尾时间；旧邀请确认不能代替本轮。"
        ]
    return "\n".join(lines) + "\n"


def write_script(case, timing=None):
    target = DEMO / SCENES[case][0] / "剧本.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(case, timing), encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for case, (title, _, _) in SCENES.items():
        proof, movie = published(case)
        require(
            hashlib.sha256(movie.read_bytes()).hexdigest() == proof["sha256"],
            f"Movie digest mismatch: {case}",
        )
        target = DEMO / title / "剧本.md"
        if args.check:
            require(
                target.exists() and target.read_text(encoding="utf-8") == render(case),
                f"Regenerate screenplay: {title}",
            )
        else:
            write_script(case)
        print(
            f"{title}: screenplay and published movie digest {'checked' if args.check else 'generated'}"
        )


if __name__ == "__main__":
    main()
