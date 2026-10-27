"""One narration source; chapter-isolated animation and measured audio share its IDs."""
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REFERENCE='AgentContractProtocol/demo/智能体互联网/scenario/{story,script}.py + web/map.js + tools/record.py'
NAME='九问IoA实验平台（Jiuwen IoA Lab）'
DESCRIPTION='面向智能体互联网的仿真验证与开放体验平台。'
SHOTS=[
 dict(id='intro',stage=0,text='我们构建了九问IoA实验平台',voice='我们构建了九问IoA实验平台。面向智能体互联网的仿真验证与开放体验平台。',focus=[],mode='cover'),
 dict(id='join',stage=1,text='把自己的九问，接入智能体互联网',voice='实验人员或者用户，可以把自己的九问Swarm注册为个人助手。',focus=['identity'],mode='join'),
 dict(id='credit',stage=2,text='每次成功登录，赠送100虚拟币',voice='个人助手每次登录，支付中心会赠送一百元虚拟货币，用于平台体验。',focus=['payment'],mode='credit'),
 dict(id='provider',stage=3,text='注册专业智能体，公开接取委托',voice='也可以把自己开发的专业智能体，向交易中心注册为公开接取委托的服务方，获得虚拟货币委托金。',focus=['registry','templates'],mode='provider'),
 dict(id='needs',stage=4,text='多位用户，同时提出不同需求',voice='智能体互联网中存在多种交互，比如委托交易。用户向个人助手提出需求：翻译品牌介绍、写朋友圈文案、给宝宝起名、润色简历、写正则、排杭州两日行程。',focus=[],mode='needs'),
 dict(id='authorization',stage=4,text='带着预算和类别，先取得用户授权',voice='每条需求带着预算上限和可委托类别。个人助手调用智能体支付授权协议，获取用户发起委托的授权。',focus=[],mode='authorization'),
 dict(id='discovery',stage=5,text='服务发现，先回答“找谁”',voice='个人助手首先调用智能体服务发现协议，找到潜在服务方列表。',focus=['registry'],mode='discovery'),
 dict(id='methods',stage=5,text='分类树与向量检索，两种发现思路',voice='这里展示两种发现思路：A to X分类树搜索，和文本向量相似度检索。本轮实际采用A to X搜索，向量索引方案仅作说明。',focus=['registry'],mode='methods'),
 dict(id='compare',stage=6,text='比较交付件、要价与验收标准',voice='个人助手随后调用A to C，也就是委托合同协议。用户横向比较不同服务方的交付件、要价等信息，筛选符合需求的服务方。',focus=['templates'],mode='compare'),
 dict(id='contract',stage=6,text='先约定怎么验，再签订合同',voice='双方协商拟定委托合同，在委托发生前，确立明确的交付成果和验收标准。',focus=['contracts','templates'],mode='contract'),
 dict(id='delivery',stage=6,text='服务方交付，平台留存签名证据',voice='服务方依照合同交付委托成果，交易中心保存交付摘要和签名证据。',focus=['notary'],mode='delivery'),
 dict(id='acceptance',stage=6,text='个人助手在本地，运行约定的验收器',voice='个人助手取件签收，调用本地SDK，在沙箱内运行合同约定的验收器。验收发生在个人助手本地。',focus=[],mode='acceptance'),
 dict(id='arbitration',stage=6,text='发生分歧，按合同判据复算并出具文书',voice='如果双方对交付标准发生分歧，可以申请仲裁。仲裁中心依据合同判据复算，出具仲裁文书。这里展示机制，不代表本轮发生了裁定。',focus=['arbitration'],mode='arbitration'),
 dict(id='payment',stage=7,text='验收通过，按用户授权与合同付款',voice='验收通过后，个人助手调用AI支付协议，基于验收结果和用户授权，向服务方支付相关费用。支付中心读卡按合同金额过账。',focus=['payment'],mode='payment'),
 dict(id='reputation',stage=7,text='委托结清，更新服务方信誉日志',voice='委托结清后，交易中心记录服务方信誉日志，为后续发现与选择提供依据。',focus=['reputation'],mode='reputation'),
 dict(id='messages',stage=8,text='个人助手之间，也有大量P2P交互',voice='除了个人助手和服务方之间的委托交易，智能体互联网还存在大量个人助手之间的P2P交互。',focus=['messages'],mode='messages'),
 dict(id='meeting',stage=8,text='按账号传消息，离线也可收取',voice='比如同事之间预约会议时间。这些消息可以通过消息中心传递。接口明确输入格式和使用范围，接收方上线后再读取和处理。',focus=['messages'],mode='meeting'),
 dict(id='value',stage=9,text='探索开放网络中的智能体经济',voice='运营一个九问IoA实验平台，可以帮助我们探索开放网络环境下，智能体经济的运转模式。',focus=[],mode='wide'),
 dict(id='internal',stage=10,text='对内：验证协议，发现技术需求',voice='对内，我们可以用它验证IoA协议在未来Agentic web中的可用性、效率、可靠性，并发现潜在技术需求。',focus=[],mode='internal'),
 dict(id='external',stage=10,text='对外：开放体验，汇聚开发者与用户',voice='对外，我们可以吸引开发者体验九问协议技术，让用户低门槛体验有趣的智能体协作，收集真实行为数据，扩大九问在智能体互联网领域的影响力。',focus=[],mode='external'),
]


def main():
 p=argparse.ArgumentParser();p.add_argument('--prepare',type=Path);p.add_argument('--timing',type=Path);a=p.parse_args()
 if a.prepare:
  a.prepare.mkdir(parents=True,exist_ok=False)
  lines=[{**s,'at':i*45} for i,s in enumerate(SHOTS)]
  (a.prepare/'captions.json').write_text(json.dumps(lines,ensure_ascii=False,indent=2),encoding='utf-8')
  (a.prepare/'evidence.json').write_text(json.dumps({'case':'global','duration':len(SHOTS)*45}),encoding='utf-8')
 timing=json.loads(a.timing.read_text(encoding='utf-8')) if a.timing else []
 times={x['id']:x['at'] for x in timing}
 rows=['# 全局视角剧本 · '+NAME,'',DESCRIPTION,'','由global_story.py生成，不手改。参考：'+REFERENCE,'','| 章节 | 起点 | 短字幕 | 旁白 | 动画 |','|---|---|---|---|---|']
 for s in SHOTS:rows.append(f"| {s['stage']} | {times.get(s['id'],'待配音')} | {s['text']} | {s['voice']} | {s['mode']} |")
 rows+=['','0—8每镜头只播放当前路径；9—10展示加速全景。接入与上下线为概念示意；本轮统计来自真实SDK运行。ANN和条件仲裁不作实际执行声明。A2C发音为A to C。真实监控实录与概念层持续区分。','']
 (ROOT/'demo/全局剧本.md').write_text('\n'.join(rows),encoding='utf-8')
 print('Global script generated')


if __name__=='__main__':main()
