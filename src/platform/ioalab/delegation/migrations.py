"""Delegation owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS needs (id TEXT PRIMARY KEY, owner_id TEXT, assistant_id TEXT, text TEXT, scope TEXT, auto_sign INTEGER DEFAULT 1, state TEXT DEFAULT 'queued', card_digest TEXT, result TEXT, created REAL, updated REAL, llm_calls INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, trace TEXT DEFAULT '[]', driver TEXT DEFAULT 'hosted');
CREATE TABLE IF NOT EXISTS confirmations (id TEXT PRIMARY KEY, owner_id TEXT, need_id TEXT, card_digest TEXT, kind TEXT, text TEXT, state TEXT DEFAULT 'pending', created REAL, answered REAL);
CREATE TABLE IF NOT EXISTS rulings (card_digest TEXT, period INTEGER, at REAL, ruling TEXT, PRIMARY KEY (card_digest, period));

CREATE TABLE IF NOT EXISTS admission_policies(service TEXT PRIMARY KEY,revision INTEGER NOT NULL,maximum INTEGER NOT NULL,accepting INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_intents(origin TEXT PRIMARY KEY,sender TEXT NOT NULL,service TEXT NOT NULL,
 scope TEXT NOT NULL,binding TEXT NOT NULL,template TEXT NOT NULL,expires REAL NOT NULL,details TEXT NOT NULL,started REAL,closed INTEGER NOT NULL DEFAULT 0,
 UNIQUE(sender,service,binding));
CREATE TABLE IF NOT EXISTS delegation_links(id TEXT PRIMARY KEY,origin TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_admissions(id TEXT PRIMARY KEY,interaction TEXT UNIQUE NOT NULL,origin TEXT UNIQUE NOT NULL,
 service TEXT NOT NULL,client TEXT NOT NULL,scope TEXT NOT NULL,binding TEXT NOT NULL,template TEXT NOT NULL,
 state TEXT NOT NULL,hold_until REAL NOT NULL,created REAL NOT NULL,committed REAL);
CREATE TABLE IF NOT EXISTS admission_operations(owner TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS admission_payments(payment_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,admission TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_signed_requests(admission TEXT PRIMARY KEY,message TEXT NOT NULL,request TEXT NOT NULL,started REAL);
CREATE TABLE IF NOT EXISTS delegation_offers(admission TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS delegation_contracts(
 admission TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,card TEXT NOT NULL,task TEXT,
 start_message TEXT,permit REAL,receipt TEXT,step TEXT,progress TEXT);
CREATE TABLE IF NOT EXISTS delegation_step_answers(
 admission TEXT NOT NULL,request TEXT NOT NULL,message TEXT NOT NULL,answer TEXT NOT NULL,
 PRIMARY KEY(admission,request));
CREATE TABLE IF NOT EXISTS delegation_termination_quotes(
 admission TEXT NOT NULL,digest TEXT NOT NULL,quote TEXT NOT NULL,PRIMARY KEY(admission,digest));

CREATE TABLE IF NOT EXISTS arbitration_cases(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,admission TEXT UNIQUE NOT NULL,
 owner TEXT NOT NULL,actor TEXT NOT NULL,client TEXT NOT NULL,service TEXT NOT NULL,
 client_owner TEXT NOT NULL,service_owner TEXT NOT NULL,request TEXT NOT NULL,basis TEXT NOT NULL,
 assessment TEXT NOT NULL,materials TEXT NOT NULL,model_binding TEXT NOT NULL,state TEXT NOT NULL,
 created REAL NOT NULL,updated REAL NOT NULL,result TEXT,ruling TEXT,decision_basis TEXT,execution TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS arbitration_operations(
 actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,owner TEXT NOT NULL,fingerprint TEXT NOT NULL,
 result TEXT NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS arbitration_attempts(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,sequence INTEGER NOT NULL,state TEXT NOT NULL,
 started REAL NOT NULL,finished REAL,result TEXT,error TEXT,UNIQUE(case_id,sequence));
CREATE TABLE IF NOT EXISTS arbitration_admin_operations(
 operator TEXT NOT NULL,id TEXT NOT NULL,case_id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(operator,id));
CREATE TABLE IF NOT EXISTS arbitration_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT,operator TEXT NOT NULL,action TEXT NOT NULL,at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS delegation_market_operations(
             owner TEXT NOT NULL,actor TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
             PRIMARY KEY(actor,id));
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
    execute(conn, PROTOCOL_CALLS)
    if 'driver' not in {row["name"] for row in conn.execute("PRAGMA table_info(needs)")}:
        conn.execute("ALTER TABLE needs ADD COLUMN driver TEXT DEFAULT 'hosted'")
    if 'progress' not in {row["name"] for row in conn.execute("PRAGMA table_info(delegation_contracts)")}:
        conn.execute("ALTER TABLE delegation_contracts ADD COLUMN progress TEXT")
    if 'decision_basis' not in {row["name"] for row in conn.execute("PRAGMA table_info(arbitration_cases)")}:
        conn.execute("ALTER TABLE arbitration_cases ADD COLUMN decision_basis TEXT")
    migrate_calls(conn)


PROTOCOL_CALLS = """
CREATE TABLE IF NOT EXISTS delegation_protocol_calls(
 message TEXT PRIMARY KEY,interaction TEXT NOT NULL,admission TEXT NOT NULL,op TEXT NOT NULL,
 state TEXT NOT NULL,last_result TEXT,last_hash TEXT,task TEXT);
"""


def migrate_calls(conn):
    from ..errors import LabError
    indexes=conn.execute('PRAGMA index_list(delegation_protocol_calls)').fetchall()
    obsolete=set()
    for index in indexes:
        quoted='"'+index['name'].replace('"','""')+'"'
        columns=[row['name'] for row in conn.execute('PRAGMA index_info('+quoted+')')]
        if index['unique'] and columns==['interaction','op']:obsolete.add(index['name'])
    if not obsolete:return
    expected=['message','interaction','admission','op','state','last_result','last_hash','task']
    if [row['name'] for row in conn.execute('PRAGMA table_info(delegation_protocol_calls)')]!=expected:
        raise LabError('not_ready','Restore or migrate the unknown original call schema before startup',status=409)
    definitions=[row['sql'] for row in conn.execute("SELECT name,type,sql FROM sqlite_master WHERE tbl_name='delegation_protocol_calls' AND type IN ('index','trigger') AND sql IS NOT NULL") if row['type']!='index' or row['name'] not in obsolete]
    conn.execute(PROTOCOL_CALLS.replace('IF NOT EXISTS delegation_protocol_calls','delegation_protocol_calls_upgraded'))
    conn.execute('INSERT INTO delegation_protocol_calls_upgraded SELECT * FROM delegation_protocol_calls')
    count=conn.execute('SELECT COUNT(*) FROM delegation_protocol_calls').fetchone()[0]
    if conn.execute('SELECT COUNT(*) FROM delegation_protocol_calls_upgraded').fetchone()[0]!=count:raise RuntimeError('Original call migration did not preserve every row')
    conn.execute('DROP TABLE delegation_protocol_calls')
    conn.execute('ALTER TABLE delegation_protocol_calls_upgraded RENAME TO delegation_protocol_calls')
    for definition in definitions:conn.execute(definition)
