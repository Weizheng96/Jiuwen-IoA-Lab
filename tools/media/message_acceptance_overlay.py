"""A labelled explanatory diagram, never an Outlook screenshot or an acceptance receipt."""
from PIL import Image, ImageDraw, ImageFont


def render(path):
    im=Image.new('RGBA',(1600,420),(9,24,40,249));d=ImageDraw.Draw(im)
    font=lambda n:ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',n)
    d.rounded_rectangle((1,1,1598,418),radius=22,outline='#497386',width=2)
    d.text((42,25),'邀请提交之后，由本人决定',font=font(40),fill='#effaff')
    d.text((42,84),'后续流程说明',font=font(25),fill='#96b6c9')
    rows=[('提交会议邀请','本轮已提交','#7de7c5'),('本人在 Outlook 接受','是否接受由王经理决定','#a5c9ed'),('会议进入个人日历','接受后生效','#a5c9ed')]
    for i,(title,subtitle,color) in enumerate(rows):
        x=40+i*525
        d.rounded_rectangle((x,145,x+470,310),radius=15,fill='#112638',outline=color,width=2)
        d.text((x+23,175),title,font=font(32),fill=color)
        d.text((x+23,241),subtitle,font=font(24),fill='#bed0dc')
        if i<2:
            d.line((x+480,227,x+512,227),fill='#7da7ba',width=4)
            d.polygon([(x+512,227),(x+501,220),(x+501,234)],fill='#7da7ba')
    d.text((42,352),'本片未展示 Outlook 接受或入历操作',font=font(24),fill='#92aab9')
    im.save(path)
