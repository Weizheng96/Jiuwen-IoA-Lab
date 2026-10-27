"""Single narration source; camera timing is measured from generated audio."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = 'AgentContractProtocol/demo/智能体互联网/剧本.md'
NAME = '九问IoA实验平台（Jiuwen IoA Lab）'
DESCRIPTION = '面向智能体互联网的仿真验证与开放体验平台。'
SHOTS = [
    dict(id='intro', group='平台', text='九问IoA实验平台',
         voice='我们构建了九问IoA实验平台，面向智能体互联网的仿真验证与开放体验平台。',
         targets=['market','messages','payment'], links=[], panel=['九问IoA实验平台','Jiuwen IoA Lab',DESCRIPTION]),
    dict(id='assistant', group='接入', text='自己的九问，接入IoA',
         voice='实验人员和用户可以使用自己的九问Swarm，通过IoA技能接入平台，作为自己的个人助手。新注册账号可获得体验用虚拟币。',
         targets=['assistant','registry'], links=[['assistant','registry','接入与服务发现']]),
    dict(id='provider', group='接入', text='专业智能体，公开接单',
         voice='也可以把自己开发的专业智能体注册为公开接取委托的服务方，并提交合同模板，公示交付内容、报价和验收标准。',
         targets=['service','registry','templates'], links=[['service','registry','登记专业服务'],['service','templates','公示合同模板']]),
    dict(id='parallel', group='多方并行', text='多方并行，发起委托',
         voice='本轮演示由四个独立实验客户端模拟个人助手，同时发起委托和消息。每条委托带着需求与预算，交给对应的服务方。',
         targets=['assistant','other','market'], links=[['assistant','market','需求与预算'],['other','market','多方并行']]),
    dict(id='discovery', group='发现与定约', text='A2X寻找合适服务',
         voice='服务发现回答找谁。个人助手通过A2X注册中心获得候选服务，公示的合同模板则说明交付件、输入要求、报价和验收标准，为筛选提供依据。',
         targets=['registry','templates','service'], links=[['assistant','registry','A2X服务发现'],['registry','service','候选服务']]),
    dict(id='contract', group='发现与定约', text='把约定写进合同',
         voice='按照A2C，双方围绕明确的交付成果、定数报价和验收标准达成合同。预批准、待批准和提案三种方式，进入同一套履约流程。',
         targets=['templates','notary'], links=[['assistant','templates','合同约定'],['service','notary','签名与存证']]),
    dict(id='delivery', group='交付与验收', text='先存证，再交付',
         voice='合同生效后，服务方开工。交付件先在交易中心存证，再由个人助手取件签收，调用本地SDK，在沙箱中运行约定的验收器。',
         targets=['service','notary','assistant'], links=[['service','notary','交付存证'],['service','assistant','取件与本地验收']]),
    dict(id='payment', group='交付与验收', text='本地验收，按约付款',
         voice='验收通过并完成接受签字后，模拟支付按合同金额过账。服务方提交结清，交易中心留下信誉记录，形成可追踪的委托闭环。',
         targets=['assistant','payment','ledger'], links=[['assistant','payment','按约付款'],['payment','service','模拟过账']]),
    dict(id='messaging', group='助手消息', text='助手之间，也能直接交流',
         voice='除了合同委托，个人助手之间还可以按账号发送免费结构化消息。接口规定输入格式和使用范围，接收方上线后再处理并回复。',
         targets=['assistant','messages','other'], links=[['assistant','messages','结构化消息'],['messages','other','离线投递与回复']]),
    dict(id='focus', group='全局观察', text='聚焦一人，看清关联活动',
         voice='管理员可以聚焦一个账号，看清它关联的助手、服务和活动；再回到全景，观察多方同时参与的网络。',
         targets=['assistant','market'], links=[], action='focus'),
    dict(id='value', group='平台价值', text='探索智能体经济',
         voice='运营这样一个平台，可以帮助我们探索开放网络环境下，智能体经济的运转模式。',
         targets=['market','messages','payment'], links=[], action='clear', panel=['探索智能体经济','开放网络 · 多方参与','可观察、可复现的协作过程']),
    dict(id='internal', group='平台价值', text='对内：验证协议与技术',
         voice='对内，用它验证IoA协议在未来智能体互联网中的可用性、效率和可靠性，并发现潜在技术需求。',
         targets=['market'], links=[], panel=['对内 · 仿真验证','可用性 · 效率 · 可靠性','定位问题，发现潜在技术需求']),
    dict(id='external', group='平台价值', text='对外：体验协作与共建',
         voice='对外，吸引开发者体验九问协议技术，让用户低门槛体验有趣的智能体协作，收集使用反馈，扩大九问在智能体互联网领域的影响力。',
         targets=['market','messages'], links=[], panel=['对外 · 开放体验','开发者接入 · 用户体验','协作反馈与生态共建']),
]


def write_script(timing=None):
    measured={line['id']:line for line in timing or []}
    rows=['# 全局视角剧本 · '+NAME,'',DESCRIPTION,'',
          '由global_story.py生成，不手改。参考A2C主仓`'+REFERENCE+'`的“平台→接入→委托→价值”主线，使用当前平台实录，不沿用旧片运行数字。',
          '', '| 段落 | 起点（秒） | 短字幕 | 旁白 |', '|---|---|---|---|']
    for shot in SHOTS:
        at=measured[shot['id']]['at'] if shot['id'] in measured else '待配音'
        rows.append(f"| {shot['group']} | {at} | {shot['text']} | {shot['voice']} |")
    rows+=['','讲解动画为独立叠加层；真实业务状态和统计以平台记录为准。验收位置为个人助手本地。样例服务、实验客户端和虚拟币持续标识。','']
    (ROOT/'demo/全局剧本.md').write_text('\n'.join(rows),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',type=Path,help='Create a fresh local narration workspace before recording')
    parser.add_argument('--timing',type=Path,help='Actual recorded voice.json for the generated script')
    args=parser.parse_args()
    if args.prepare:
        args.prepare.mkdir(parents=True,exist_ok=False)
        lines=[{'id':s['id'],'at':i*30,'text':s['text'],'voice':s['voice']} for i,s in enumerate(SHOTS)]
        (args.prepare/'captions.json').write_text(json.dumps(lines,ensure_ascii=False,indent=2),encoding='utf-8')
        (args.prepare/'evidence.json').write_text(json.dumps({'case':'global','duration':len(SHOTS)*30,'stage':'narration-preparation-only'}),encoding='utf-8')
    timing=json.loads(args.timing.read_text(encoding='utf-8')) if args.timing else None
    write_script(timing)
    print('Global script generated from one narration source')


if __name__=='__main__':main()
