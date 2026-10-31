"""Read-only category decisions over the actual A2X leaf directory."""
import copy

from a2c.codec import digest
from ..errors import LabError


class CategoryPreview:
    def __init__(self,categories,choose):
        self.categories,self.choose=categories,choose

    def preview(self,body):
        required={'name','description','tags'}
        if not isinstance(body,dict) or not required<=set(body) or set(body)-required-{'categoryId'}:
            raise LabError('invalid_input','Use public name, description, tags and optional categoryId')
        for name,limit in (('name',120),('description',8000)):
            if not isinstance(body[name],str) or not 1<=len(body[name].strip())<=limit:
                raise LabError('invalid_input','Use a bounded public service '+name)
        if not isinstance(body['tags'],list) or len(body['tags'])>20 or any(not isinstance(tag,str) or not 1<=len(tag)<=80 for tag in body['tags']):
            raise LabError('invalid_input','Use at most20 bounded public tags')
        categories=[{key:copy.deepcopy(item[key]) for key in ('id','name','description','decision_rule','path','isFallback')}
                    for item in self.categories()]
        available={item['id']:item for item in categories}
        result={'status':'choose_category','categoryId':None,'categoryPath':[],
                'taxonomyDigest':digest({'categories':categories}),'categories':categories}
        def matched(category,reason):
            return {**result,'status':'matched','categoryId':category['id'],'categoryPath':category['path'],'reason':reason}
        if 'categoryId' in body:
            value=body['categoryId']
            if not isinstance(value,str) or value not in available:
                raise LabError('category_required','Choose an existing A2X leaf category',status=409,recovery='choose_category')
            return matched(available[value],'使用用户指定的实际A2X叶类')
        candidates=[item for item in categories if not item['isFallback']]
        if not candidates:return {**result,'reason':'实际分类目录尚未就绪，请先选择或配置分类'}
        try:choice=self.choose({name:body[name] for name in required},candidates)
        except Exception:
            return {**result,'reason':'自动分类暂不可用，请从实际分类中选择'}
        if choice is None:return {**result,'reason':'自动分类未配置，请从实际分类中选择'}
        if (not isinstance(choice,dict) or set(choice)!={'categoryId','reason'} or
            not isinstance(choice.get('reason'),str) or not 1<=len(choice['reason'].strip())<=1000 or
            not isinstance(choice.get('categoryId'),str) or choice['categoryId'] not in {item['id'] for item in candidates}):
            return {**result,'reason':'说明不足、分类含糊或匹配结果无效，请选择实际分类'}
        return matched(available[choice['categoryId']],choice['reason'])
