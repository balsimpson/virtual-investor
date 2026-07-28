import json, os, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).parents[1]
PORT=ROOT/'scripts'/'portfolio.py'
SWEEP=ROOT/'scripts'/'harper-intel.py'
CLASSIFIER=ROOT/'scripts'/'harper-intel-classifier.py'

def run(cmd, env):
    r=subprocess.run(cmd,text=True,capture_output=True,env=env)
    assert r.returncode==0, r.stdout+r.stderr
    return r

def test_sweep_and_classifier(tmp_path):
    db=tmp_path/'portfolio.db'; env=os.environ.copy(); env['PYTHONPATH']=''; env['VIRTUAL_INVESTOR_DB']=str(db); env['VIRTUAL_INVESTOR_DISABLE_SYNC']='1'
    run([sys.executable,str(PORT),'init'],env)
    rss=tmp_path/'feed.xml'
    rss.write_text('''<rss><channel><item><title>RBI keeps repo rate unchanged</title><link>https://example.com/rbi</link><description>India policy update</description></item><item><title>European weather report</title><link>https://example.com/weather</link><description>Rain expected</description></item></channel></rss>''')
    run([sys.executable,str(PORT),'intel-sources','add',rss.as_uri(),'--name','Test'],env)
    out=json.loads(run([sys.executable,str(SWEEP),'--db',str(db)],env).stdout)
    assert out['stored']==1 and out['staged']==1
    packet=tmp_path/'packet.json'; run([sys.executable,str(CLASSIFIER),'--db',str(db),'--export',str(packet)],env)
    article=json.loads(packet.read_text())['articles'][0]
    decisions=tmp_path/'decisions.json'; decisions.write_text(json.dumps([{'id':article['id'],'relevant':True,'tickers':['TCS.NS'],'entity_patterns':['European Central Bank']}]))
    result=json.loads(run([sys.executable,str(CLASSIFIER),'--db',str(db),'--apply',str(decisions)],env).stdout)
    assert result['passed']==1 and result['new_patterns']==1
    status=json.loads(run([sys.executable,str(PORT),'intel-sources','staging','status'],env).stdout)
    assert status['staged_total']==0
