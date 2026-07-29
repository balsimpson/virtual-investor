#!/usr/bin/env python3
"""Apply an agent-produced batch classification to staged intel.

Use --export to emit the review packet. The Hermes agent classifies that packet
and writes a JSON decisions file; --apply makes the deterministic DB changes.
"""
from __future__ import annotations
import argparse,json,os,re,sqlite3
from pathlib import Path
from harper_intel_core import insert_article, parse_classifier_decisions, update_source_relevance, utcnow
DB=Path(os.environ.get("VIRTUAL_INVESTOR_DB",Path.home()/".hermes/data/virtual-investor/portfolio.db"))

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--db",type=Path,default=DB); g=ap.add_mutually_exclusive_group(required=True); g.add_argument("--export",type=Path); g.add_argument("--apply",type=Path); ap.add_argument("--limit",type=int,default=80); args=ap.parse_args()
 with sqlite3.connect(args.db) as conn:
  conn.row_factory=sqlite3.Row
  rows=conn.execute("SELECT st.id,st.source_id,st.title,st.link,st.summary,s.name source_name FROM intel_relevance_staging st JOIN intel_sources s ON s.id=st.source_id WHERE st.batch_id IS NULL ORDER BY st.id LIMIT ?",(args.limit,)).fetchall()
  if args.export:
   args.export.write_text(json.dumps({"instructions":"For each item return id, relevant boolean, tickers array, and up to 3 reusable entity_patterns.","articles":[dict(r) for r in rows]},indent=2)); print(json.dumps({"exported":len(rows),"path":str(args.export)})); return
  decisions=parse_classifier_decisions(args.apply); batch=f"classifier_{utcnow().replace(':','').replace('-','').replace('.','_')}"; passed=rejected=new_patterns=0
  for row in rows:
   d=decisions.get(int(row["id"]));
   if d is None: continue
   relevant=bool(d.get("relevant")); suffix="passed" if relevant else "rejected"
   if relevant:
    insert_article(conn,row["source_id"],row["title"],row["link"],row["summary"],d.get("tickers",[])); passed+=1; update_source_relevance(conn,row["source_id"],True,True)
    for pat in d.get("entity_patterns",[])[:3]:
     pat=str(pat).strip()
     if len(pat)<3 or len(pat)>80: continue
     before=conn.total_changes
     conn.execute("INSERT OR IGNORE INTO intel_relevance_patterns(pattern,pattern_type,weight,source,created_at) VALUES(?, 'entity',10,'llm_rescue',?)",(re.escape(pat),utcnow()))
     if conn.total_changes>before: new_patterns+=1
   else:
    rejected+=1; update_source_relevance(conn,row["source_id"],False)
   conn.execute("UPDATE intel_relevance_staging SET batch_id=? WHERE id=?",(f"{batch}_{suffix}",row["id"]))
  conn.execute("INSERT INTO intel_relevance_batches(id,total_articles,passed,rejected,reviewed_for_patterns,new_patterns,created_at) VALUES(?,?,?,?,?,?,?)",(batch,passed+rejected,passed,rejected,passed,new_patterns,utcnow()))
  conn.commit(); print(json.dumps({"batch_id":batch,"processed":passed+rejected,"passed":passed,"rejected":rejected,"new_patterns":new_patterns},indent=2))
if __name__=="__main__": main()
