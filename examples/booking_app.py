"""Local-only booking runner for a validated JSON delivery. No generated code executes."""
import argparse,json,sqlite3,uuid
from datetime import datetime,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path


class Bookings:
    def __init__(self,config,database):
        self.config=config;self.database=str(database)
        assert config['format']=='ioalab.booking'
        assert type(config['capacity']) is int and 1<=config['capacity']<=10
        assert 0<=config['open']<config['close']<=24
        assert config.get('idempotent') is True and config.get('allowCancel') is True
        self.services={x['name']:x['minutes'] for x in config['services']}
        assert len(self.services)==len(config['services']) and all(type(n) is int and 15<=n<=240 for n in self.services.values())
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY, request TEXT UNIQUE, name TEXT, service TEXT, start TEXT, end TEXT, cancelled INTEGER DEFAULT 0)')

    def connect(self):
        db=sqlite3.connect(self.database,timeout=20);db.row_factory=sqlite3.Row;return db

    def list(self):
        with self.connect() as db:return [dict(x) for x in db.execute('SELECT * FROM bookings ORDER BY start')]

    def book(self,request,name,service,start):
        if not all(isinstance(x,str) and 0<len(x)<=100 for x in (request,name,service,start)):raise ValueError('Invalid booking fields')
        if service not in self.services:raise ValueError('Unknown service')
        a=datetime.fromisoformat(start)
        if a.tzinfo is not None:raise ValueError('Use local wall time')
        b=a+timedelta(minutes=self.services[service])
        if not (a.date()==b.date() and a.hour>=self.config['open'] and b<=a.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(hours=self.config['close'])):raise ValueError('Outside opening hours')
        start,end=a.isoformat(),b.isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM bookings WHERE request=?',(request,)).fetchone()
            if old:
                if (old['name'],old['service'],old['start'])!=(name,service,start):raise ValueError('Idempotency key reused with different request')
                return dict(old)
            # Count the maximum simultaneous occupancy, rather than all overlaps.
            rows=db.execute('SELECT start,end FROM bookings WHERE cancelled=0 AND start<? AND end>?',(end,start)).fetchall()
            points=[(max(start,r['start']),1) for r in rows]+[(min(end,r['end']),-1) for r in rows]
            used=0
            for _,change in sorted(points):
                used+=change
                if used>=self.config['capacity']:raise ValueError('该时段已满')
            ident=uuid.uuid4().hex[:12]
            db.execute('INSERT INTO bookings(id,request,name,service,start,end) VALUES(?,?,?,?,?,?)',(ident,request,name,service,start,end))
            return dict(db.execute('SELECT * FROM bookings WHERE id=?',(ident,)).fetchone())

    def cancel(self,ident):
        with self.connect() as db:
            if not db.execute('SELECT id FROM bookings WHERE id=?',(ident,)).fetchone():raise ValueError('Unknown booking')
            db.execute('UPDATE bookings SET cancelled=1 WHERE id=?',(ident,))
        return {'status':'cancelled','id':ident}


HTML='''<!doctype html><meta charset="utf-8"><title>本地预约工具</title><style>body{background:#101a2b;color:#e5efff;font:22px "Microsoft YaHei";max-width:850px;margin:60px auto}input,select,button{font:20px "Microsoft YaHei";padding:12px;margin:8px;border-radius:8px}h1{color:#62d6cb}article{padding:15px;border:1px solid #405674;margin:12px}small{color:#9bb0cb}</style><h1 id="shop"></h1><p>本地预约工具 · IoA合同交付配置</p><small id="scope"></small><form id="form"><p><input id="name" placeholder="客户称呼" required><select id="service"></select></p><input id="start" type="datetime-local" required><button>预约</button></form><p id="result"></p><section id="items"></section><script>
let request=crypto.randomUUID();let config;const el=id=>document.getElementById(id);async function api(path,data){let r=await fetch(path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});let d=await r.json();if(!r.ok)throw Error(d.error);return d}async function refresh(){let list=await api('/bookings');el('items').replaceChildren();for(let x of list){let a=document.createElement('article');a.textContent=x.name+' · '+x.service+' · '+x.start.replace('T',' ')+' · '+(x.cancelled?'已取消':'预约成功');if(!x.cancelled){let b=document.createElement('button');b.textContent='取消预约';b.onclick=async()=>{await api('/cancel',{id:x.id});await refresh()};a.append(b)}el('items').append(a)}}
api('/config').then(c=>{config=c;el('shop').textContent=c.shop;el('scope').textContent='营业 '+c.open+':00—'+c.close+':00 · 同时接待 '+c.capacity+' 人 · 支持取消与幂等重试';for(let s of c.services){let o=document.createElement('option');o.value=s.name;o.textContent=s.name+' '+s.minutes+'分钟';el('service').append(o)}refresh()});el('form').onsubmit=async e=>{e.preventDefault();try{await api('/book',{request,name:el('name').value,service:el('service').value,start:el('start').value});request=crypto.randomUUID();el('result').textContent='预约成功';await refresh()}catch(e){el('result').textContent=e.message}};
</script>'''


def serve(config,database,port):
    bookings=Bookings(config,database)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data,media='application/json'):
            blob=(json.dumps(data,ensure_ascii=False) if media=='application/json' else data).encode('utf-8')
            self.send_response(code);self.send_header('Content-Type',media+'; charset=utf-8');self.send_header('Content-Length',str(len(blob)));self.end_headers();self.wfile.write(blob)
        def do_GET(self):
            if self.path=='/':self.reply(200,HTML,'text/html')
            elif self.path=='/config':self.reply(200,config)
            elif self.path=='/bookings':self.reply(200,bookings.list())
            else:self.reply(404,{'error':'Not found'})
        def do_POST(self):
            # Local demo app: reject cross-origin websites and bound request sizes.
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{port}'):
                self.reply(403,{'error':'Origin rejected'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(length))
                if self.path=='/book':result=bookings.book(**data)
                elif self.path=='/cancel':
                    if set(data)!={'id'}:raise ValueError('Invalid cancellation fields')
                    result=bookings.cancel(data['id'])
                else:self.reply(404,{'error':'Not found'});return
                self.reply(200,result)
            except (ValueError,TypeError,KeyError):self.reply(400,{'error':'预约失败：格式、时段、容量或请求标识不符合规则'})
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--port',type=int,default=8768);a=p.parse_args()
    serve(json.loads(a.config.read_text(encoding='utf-8')),a.database,a.port)
