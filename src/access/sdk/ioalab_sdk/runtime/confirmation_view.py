"""Deterministic human-readable projection of the original confirmation binding."""
import copy


LABELS = {
    'purpose':'委托目标', 'budget':'总预算上限', 'proposalFeeLimit':'拟卡费上限（含在预算内）',
    'currency':'记账单位', 'expiresAt':'有效期限', 'categoryIds':'服务范围', 'revision':'授权修订',
    'contractConfirmation':'后续独立确认', 'service':'服务方', 'contractRef':'合同引用',
    'card':'具体合同', 'materials':'发送材料及阶段', 'period':'本期期次', 'amount':'本次金额',
    'total':'合同总额', 'price':'价款', 'deposit':'定金', 'deliverables':'交付物',
    'acceptance':'验收要求', 'notCovered':'未覆盖事项', 'inputs':'所需材料', 'sendAt':'发送阶段',
    'spec':'交付规格', 'specSchema':'规格约束', 'deadlines':'期限', 'steps':'合同环节',
    'notice':'说明', 'stepRequest':'当前合同环节请求', 'payment':'付款', 'paymentStatus':'付款状态',
    'client':'委托方', 'provider':'服务方', 'lineItems':'价款明细', 'derived':'合同金额及期限',
    'criteria':'验收条件', 'verifiers':'验收器', 'acceptWithin':'验收时限', 'signers':'签署要求',
    'description':'说明', 'name':'名称', 'proposalFee':'拟卡费', 'authorizableSteps':'需独立确认的合同环节',
    'periods':'分期价款', 'deliverable':'交付物', 'formation':'成约方式', 'materialsForReview':'签前审查材料',
}


def readable(value):
    if value is None:return '未提供'
    if isinstance(value, bool):return '是' if value else '否'
    if isinstance(value, list):return [readable(item) for item in value]
    if isinstance(value, dict):
        # Signature bytes remain available in the unmodified original details.
        return {LABELS.get(key,key):readable(item) for key,item in value.items()
                if key not in ('signature','signatures','jwk','jwks','publicKey','privateKey')}
    return value


def present(item):
    """Keep every original field and digest; the projection never changes approval."""
    result = copy.deepcopy(item)
    preview = result['binding']['preview']
    sections=[]
    if isinstance(preview.get('card'),dict):
        derived=preview.get('derived',preview['card'].get('derived',{}))
        sections=[{'label':'合同总额','value':readable(derived.get('total'))},
                  {'label':'本次金额','value':readable(preview.get('amount',preview.get('paymentAmount')))},
                  {'label':'拟卡费','value':readable(preview.get('proposalFee'))}]
        deliveries=preview.get('deliverables')
        sections.append({'label':'验收未覆盖事项','value':readable(
            [{'deliverable':value.get('name'),'notCovered':value.get('acceptance',{}).get('notCovered')}
             for value in deliveries] if isinstance(deliveries,list) else None)})
    sections.extend({'label':LABELS.get(key,key),'value':readable(value)}
                    for key,value in preview.items() if key not in ('title','card'))
    if 'card' in preview:
        # The complete original (including keys and signatures) remains folded.
        sections.append({'label':'合同对象','value':readable({key:preview['card'][key]
            for key in ('client','provider','spec','derived','acceptance','deadlines') if key in preview['card']})})
    result['view'] = {'title': preview.get('title',result['action']),
                      'sections':sections,
                      'notice':'本决定仅覆盖本次显示的原操作。后续合同环节、验收及付款按各自确认执行。'}
    return result
