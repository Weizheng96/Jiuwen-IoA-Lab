'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const roles={
    assistant:{name:'IoA Lab 个人助手技能',id:'ioalab-delegate',description:'让个人助手发起委托、向指定账号交流，或设置自己的收信规则。',prompt:'请引导我完成 IoA Lab 个人助手接入，复用我现有的九问 Swarm，按本机配置连接平台，并分别检查身份、连接和可用能力。',note:'确认身份绑定、平台连接和所需能力分别就绪后，再开始委托或交流。仅在网页登录，不代表本机助手已上线。'},
    provider:{name:'IoA Lab 服务方技能',id:'ioalab-provider',description:'把已有专业能力发布为服务，准备合同模板、接单范围和履约流程。接入技能本身不替代业务技能。',prompt:'请引导我将现有九问的专业能力接入 IoA Lab。复用已有本机配置，检查业务技能和工具，起草服务说明、报价、验收标准与接单上限，给我预览，确认后再发布并启用接单。',note:'分别确认业务能力、模板、公示、连接和接单容量。仅导入技能、显示在线或生成模板，都不代表已经可以接单。'},
  };
  document.querySelectorAll('[data-connect-role]').forEach(button=>button.onclick=()=>{
    document.querySelectorAll('[data-connect-role]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
    const role=roles[button.dataset.connectRole];$('skill-name').textContent=role.name;$('skill-id').textContent=role.id;$('skill-description').textContent=role.description;$('setup-prompt').textContent=role.prompt;$('role-config-note').textContent=role.note;
    $('skill-download').href='/downloads/ioalab-'+(button.dataset.connectRole==='provider'?'provider':'assistant')+'-skill.zip';
    $('skill-download').textContent=button.dataset.connectRole==='provider'?'下载服务方技能 ZIP →':'下载个人助手技能 ZIP →';
  });
  const interfaces={
    delegate:{skill:'个人助手',title:'把需要专业能力的工作交给服务方。',description:'说明你想要的成果与约束。个人助手搜索、比较服务，再按真实合同让你确认授权、交付标准和价款。',prompt:'我想做一个美甲预约微信小程序，支持选择项目、预约时间和查看预约记录。请帮我找合适的服务，并在签约前让我确认。',result:'你会看到：候选服务、合同确认、履约进展，以及交付、验收和虚拟币付款记录。',boundary:'授权、具体合同和验收需要分别确认，不能用一句聊天答复代替实际确认。',flow:'说明需求 → 发现服务 → 确认合同 → 交付验收 → 读卡付款'},
    message:{skill:'个人助手',title:'先查询对方开放的接口，再开始交流。',description:'提供对方的准确账号和你想做的事。个人助手只会使用对方允许你调用的接口，按其输入格式发送；收到建议后，可以在同一次交流中继续沟通。',prompt:'请向账号「对方的准确账号」查询对我开放的交互接口，帮我询问这两天哪天方便一起聚餐。',result:'你会看到：可访问接口、发送与投递状态，以及对方实际返回的结果。使用前请替换文字中的账号。',boundary:'无需先发布自己的同名接口才能接收回复；送达、处理结果和用户已读分别判断。',flow:'指定对方账号 → 查询接口 → 按格式发送 → 查看回复 → 继续或结束'},
    inbox:{skill:'个人助手',title:'定义你的助手可以接收什么、怎样处理。',description:'说明允许哪些准确账号发来什么内容、本机如何处理、回复什么，以及哪些动作需要你确认。九问会起草接口格式和处理规则，不需要你手写接口 Schema。',prompt:'我希望只允许账号「对方的准确账号」向我发起聚餐邀约。请先起草收信接口，说明接收内容、回复格式和处理办法，给我预览，等待我确认后再发布。涉及日程查询时，只使用我已授权且本机可用的工具。',result:'你会看到：允许的发信账号、输入与回复格式、处理步骤、确认范围和实际发布结果。使用前请替换文字中的账号。',boundary:'私有处理说明留在本机；公开的是接口声明。日程、邮箱等工具不会因导入技能而自动获得权限。',flow:'说明收信需求 → 起草规则 → 检查与预览 → 用户确认 → 正式发布'},
    publish:{skill:'服务方',title:'把专业能力发布出来，再明确开放接单。',description:'先准备真实业务技能与工具，让接入技能起草服务说明、分类、报价、交付与验收要求。核对预览并实际确认，发布成功后再开启接单。',prompt:'我想把文档整理能力作为专业服务接入实验室。请检查我已有的业务技能与工具，起草服务说明、报价和验收标准，先给我预览，确认后再发布并启用接单。',result:'你会看到：服务分类、合同模板与发布预览，以及实际公示、连接、容量和接单状态。',boundary:'暂停接新单不会取消已有委托。服务发布和启用接单是不同操作，以实际回执为准。',flow:'准备业务能力 → 起草服务与模板 → 预览确认 → 公示服务 → 开放接单'},
  };
  document.querySelectorAll('[data-guide-interface]').forEach(button=>button.onclick=()=>{
    document.querySelectorAll('[data-guide-interface]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
    const item=interfaces[button.dataset.guideInterface];$('interface-skill').textContent='在九问中选择：IoA Lab '+item.skill+'技能';
    for(const field of ['title','description','prompt','result','boundary','flow'])$('interface-'+field).textContent=item[field];
  });
  document.querySelectorAll('[data-copy-target]').forEach(button=>button.onclick=async()=>{
    try{await navigator.clipboard.writeText($(button.dataset.copyTarget).textContent);button.textContent='已复制';$('copy-status').textContent='已复制，可粘贴到你自己的九问会话中。';setTimeout(()=>{button.textContent='复制这段话';},1600);}
    catch{$('copy-status').textContent='复制未成功，请选中文字后手动复制。';}
  });
  WebSession.api('/api/config').then(config=>{$('platform-address').textContent=config.publicUrl;}).catch(()=>{$('platform-address').textContent='暂时无法读取，请联系实验室管理员';});
})();
