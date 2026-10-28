"""One source of short captions and narration for the two real scenarios."""
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STORIES={
 'delegation':[
  ('cover','一句需求，九问完成合同委托','小满美甲店需要一个预约工具。我们用真实九问，完成从找服务到交付验收的全过程。'),
  ('need','需求带着预算，交给自己的九问','店主说明营业时段、服务项目、同时接待人数和取消规则，预算上限五百虚拟币。'),
  ('discovery','A2X发现服务，读取已验签模板','九问调用本地IoA技能，通过A to X寻找服务，并读取完整的已验签合同模板。'),
  ('quotes','两份真实提案，比较报价和判据','两项服务分别给出提案。基础版二百五十九，可靠版二百九十九虚拟币；区别还在于验收判据的覆盖范围。'),
  ('choice','选择可靠版，明确交付和验收标准','店主选择可靠版。十项配置判据写入合同，包含容量、营业时段、服务时长、取消和幂等要求。'),
  ('contract','确认合同后，才开工','店主确认后，本地SDK完成合同签字并发出开工请求。A to C把约定和履约证据串在一起。'),
  ('production','独立服务进程，按需启动九问','官方服务在独立进程中，收到这一单才调用九问Swarm生产。这里使用本单需求生成结果，不以样件代替。'),
  ('delivery','交付先存证，个人助手再取件','服务方通过SDK自测并交付，交易中心记录交付摘要与时间戳。个人助手收到存证通知后取件。'),
  ('verification','在个人助手本地，运行WASM验收','九问调用本地A to C SDK，在WASM沙箱中运行合同约定的验收器。聊天中的完成声明，不能代替验收结果。'),
  ('payment','确认验收，按合同金额付款','验收通过后，店主再次确认接受。SDK签署验收结论，并经模拟支付按合同金额过账，使用的是虚拟币。'),
  ('provider','切换服务方，核对同一笔委托','切换到服务方账号，按独立服务查看同一合同的完成记录和实际收入。合同金额与已收款分别展示。'),
  ('tool','运行交付配置，体验预约和取消','这单交付的是预约工具配置，配合固定的本地运行器使用。实际登记预约、查看记录并取消，验证交付后的体验。'),
  ('close','发现、定约、交付、验收、支付可追踪','从一条需求，到双方都可追踪的合同闭环。平台负责发现与证据，用户自己的九问负责签约和本地验收。')],
 'messaging':[
  ('cover','个人助手之间，也能直接协作','除了委托专业服务，九问个人助手之间也可以按账号交流。这一段演示留言和会议预约。'),
  ('policy','公开使用范围，接口本身不进服务市场','用户A只允许指定账号调用自己的免费接口。邮箱形式只是账号字符串，不代表平台认证了企业邮箱归属。'),
  ('schedule','本地时间安排，可查看、可编辑','A在本地保存可编辑的时间安排，预先设定可预约规则，并登记已有会议。这里没有读取私人Outlook日历。'),
  ('lookup','先查询接口，再按格式发送','用户B让自己的九问查询A的接口。接口给出明确的输入格式，SDK据此构造结构化消息。'),
  ('forward','消息中心投递，A的九问网页收到留言','留言经消息中心传递，接收方客户端读取后，转发到A的真实九问网页会话。投递成功与用户已读分别记录。'),
  ('busy','已有安排冲突，这个时间不可预约','B先申请一个已有会议的时段。A的本地处理器依据时间安排和预授权规则检查，返回这个时间不可预约。'),
  ('reschedule','B提出新时间，A按规则同意','B改约空闲时段。A再次核对本地安排和待接受预约，符合规则后，同意本次预约并提交邀请。'),
  ('invitation','通知邮箱发出邀请，等待用户接受','邀请由助手专用的AgentMail通知邮箱发出，收件人是管理员的Outlook。提交邀请和接受入历，是两个不同状态。'),
  ('outlook','在Outlook接受邀请，会议进入日历','收件人在真实Outlook界面接受邀请，再到日历查看对应的会议。平台不直接写入私人Outlook日历。'),
  ('close','免费消息，连接用户自己的个人助手','结构化接口、使用范围、离线收取和处理回执，让用户自己的个人助手可以低门槛协作。')]
}


def main():
 p=argparse.ArgumentParser();p.add_argument('--case',choices=STORIES,required=True);p.add_argument('--prepare',type=Path);a=p.parse_args();lines=[{'id':i,'text':t,'voice':v,'at':n*45} for n,(i,t,v) in enumerate(STORIES[a.case])]
 if a.prepare:
  a.prepare.mkdir(parents=True,exist_ok=False);(a.prepare/'captions.json').write_text(json.dumps(lines,ensure_ascii=False,indent=2),encoding='utf-8');(a.prepare/'evidence.json').write_text(json.dumps({'case':a.case,'duration':len(lines)*45}),encoding='utf-8')
 rows=['# '+('美甲委托' if a.case=='delegation' else '助手交互')+'剧本','','由case_story.py生成，不手改。镜头来自实际运行，等待可剪短并标记。','','| 镜头 | 短字幕 | 旁白 |','|---|---|---|']
 rows += ['| '+i+' | '+t+' | '+v+' |' for i,t,v in STORIES[a.case]]
 (ROOT/'demo'/('委托剧本.md' if a.case=='delegation' else '交互剧本.md')).write_text('\n'.join(rows)+'\n',encoding='utf-8')


if __name__=='__main__':main()
