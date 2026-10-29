"""One source of short captions and narration for the two real scenarios."""

import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORIES = {
    "delegation": [
        (
            "cover",
            "一句需求，九问完成合同委托",
            "小美美甲店需要一个预约小程序。我们用真实九问，完成从找服务到交付验收的全过程。",
        ),
        (
            "need",
            "选好IoA技能，一句话发起委托",
            "店主只说一句需求：开发美甲预约小程序，每个时段最多两人，顾客可以取消，预算上限五百虚拟币。九问自动调用已经安装的IoA技能。",
        ),
        (
            "authorization",
            "先授权委托，合同与验收仍由店主确认",
            "九问先申请发起委托的授权：只用于预约小程序开发，总预算五百，三家拟卡费合计不超过三虚拟币，七天有效。店主确认后才继续。",
        ),
        (
            "discovery",
            "A2X发现服务，读取已验签模板",
            "九问调用本地IoA技能，通过A to X寻找服务，并读取完整的已验签合同模板。",
        ),
        (
            "quotes",
            "三份真实提案，比较报价和判据",
            "七家候选逐步筛选，剩下灵犀小程序、快搭建站和码上飞，分别给出二百三十九、二百五十九和二百九十九虚拟币的真实签名提案。",
        ),
        (
            "choice",
            "九问比较后推荐码上飞",
            "灵犀使用模型观察，快搭有五项基础行为测试。码上飞贵四十虚拟币，但有十项行为测试，覆盖抢约容量、重复提交和弱网重试，九问据此推荐码上飞。",
        ),
        (
            "contract",
            "确认合同后，才开工",
            "店主确认后，本地SDK完成合同签字并发出开工请求。A to C把约定和履约证据串在一起。",
        ),
        (
            "registration",
            "实名注册，一事一授权",
            "服务方发起合同事先列明的实名注册环节，九问再次请求店主授权。本地演示仅记录这次授权，不提交真实身份资料或声称微信已经上线。",
        ),
        (
            "production",
            "独立服务进程，按需启动九问",
            "官方服务在独立进程中，收到这一单才调用九问Swarm生产。这里使用本单需求生成结果，不以样件代替。",
        ),
        (
            "delivery",
            "交付先存证，个人助手再取件",
            "服务方通过SDK自测并交付，委托中心记录交付摘要与时间戳。个人助手收到存证通知后取件。",
        ),
        (
            "verification",
            "在个人助手本地，运行WASM验收",
            "九问调用本地A to C SDK，在WASM沙箱中运行合同约定的验收器。聊天中的完成声明，不能代替验收结果。",
        ),
        (
            "payment",
            "确认验收，按合同金额付款",
            "验收通过后，店主再次确认接受。SDK签署验收结论，并经模拟支付按合同金额过账，使用的是虚拟币。",
        ),
        (
            "provider",
            "切换服务方，核对同一笔委托",
            "切换到服务方账号，按独立服务查看同一合同的完成记录和实际收入。合同金额与已收款分别展示。",
        ),
        (
            "tool",
            "运行交付工具，体验预约和取消",
            "本单交付了预约工具配置、核心代码和小程序页面源码。现在运行本地预约工具，登记、查看并取消一笔预约，核对实际体验。",
        ),
        (
            "close",
            "发现、定约、交付、验收、支付可追踪",
            "从一条需求，到双方都可追踪的合同闭环。平台负责发现与证据，用户自己的九问负责签约和本地验收。",
        ),
    ],
    "messaging": [
        (
            "cover",
            "个人助手之间，也能直接交互",
            "除了向专业服务发起委托，个人助手之间也可以直接交互。我们以王经理和赵专员的个人助手为例，看看消息如何传递。",
        ),
        (
            "policy",
            "自己定义接口，也能限定使用范围",
            "个人助手可以定义自己的交互接口，约定输入格式，并限定哪些账号可以使用。消息中心按这些规则接收和传递消息。",
        ),
        (
            "schedule",
            "王经理定义预约接口，邀请交本人审批",
            "王经理的个人助手定义了预约会议的交互，允许属于指定范围的个人助手，通过指定格式向其预约会议。如果时间允许，个人助手会把会议邀请交给王经理审批；否则会代王经理拒绝，并重新协商会议时间。",
        ),
        (
            "lookup",
            "赵专员发起预约，先读接口再构造消息",
            "赵专员让自己的个人助手尽快约王经理开会。赵专员的个人助手读取王经理个人助手对其开放的交互接口，构造结构化交互消息，再交给平台的消息中心传递。",
        ),
        (
            "forward",
            "消息中心传递，王经理的九问收到留言",
            "留言先送到消息中心。王经理的个人助手上线收取后，把留言呈现在王经理的九问网页中；发送方可以查询处理回执。",
        ),
        (
            "busy",
            "已有安排冲突，这个时间不可预约",
            "这次申请的时间与王经理已有安排冲突。王经理的个人助手代为拒绝，冲突回执经过消息中心，回到赵专员的个人助手。",
        ),
        (
            "reschedule",
            "赵专员改约，王经理的助手再次检查",
            "赵专员的个人助手收到回执后，提出另一个时间。王经理的个人助手再次检查，符合预约规则后，准备会议邀请。",
        ),
        (
            "invitation",
            "邀请已提交，本人接受后入历",
            "会议邀请已经提交。接下来，由王经理在Outlook中决定是否接受；接受后，会议才会进入他的个人日历。",
        ),
        (
            "outlook",
            "在Outlook接受邀请，会议进入日历",
            "收件人在真实Outlook界面接受邀请，再到日历查看对应的会议。平台不直接写入私人Outlook日历。",
        ),
        (
            "close",
            "结构化交互，连接用户自己的个人助手",
            "从接口发现，到消息传递和处理回执，平台连接起用户自己的个人助手。留言、预约与更多日常交互，都能沿着同一套机制扩展。",
        ),
    ],
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=STORIES, required=True)
    p.add_argument("--prepare", type=Path)
    p.add_argument('--acceptance-presentation',choices=('recorded','narrated'),default='recorded')
    a = p.parse_args()
    if a.acceptance_presentation=='narrated' and a.case!='messaging':p.error('Narrated acceptance applies to messaging')
    lines = [
        {"id": i, "text": t, "voice": v, "at": n * 45}
        for n, (i, t, v) in enumerate(s for s in STORIES[a.case] if not (a.acceptance_presentation=='narrated' and s[0]=='outlook'))
    ]
    if a.prepare:
        a.prepare.mkdir(parents=True, exist_ok=False)
        (a.prepare / "captions.json").write_text(
            json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (a.prepare / "evidence.json").write_text(
            json.dumps({"case": a.case, "duration": len(lines) * 45}), encoding="utf-8"
        )
    from script_documents import write_script

    print(write_script(a.case))


if __name__ == "__main__":
    main()
