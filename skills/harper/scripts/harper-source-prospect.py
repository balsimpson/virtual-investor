#!/usr/bin/env python3
"""Safely add explicitly supplied feed prospects after basic URL validation."""
from __future__ import annotations
import argparse,json,os,sqlite3,urllib.parse
from pathlib import Path
from harper_intel_core import utcnow
DB=Path(os.environ.get("VIRTUAL_INVESTOR_DB",Path.home()/".hermes/data/virtual-investor/portfolio.db"))
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("prospects",type=Path,help="JSON list: [{name,feed_url}]"); ap.add_argument("--db",type=Path,default=DB); args=ap.parse_args(); items=json.loads(args.prospects.read_text()); added=[]; skipped=[]
 with sqlite3.connect(args.db) as conn:
  for item in items:
   url=str(item.get("feed_url","")).strip(); parsed=urllib.parse.urlparse(url)
   if parsed.scheme not in ("http","https") or not parsed.netloc: skipped.append({"feed_url":url,"reason":"invalid URL"}); continue
   cur=conn.execute("INSERT OR IGNORE INTO intel_sources(name,feed_url,source_type,enabled,added_at) VALUES(?,?, 'rss',1,?)",(item.get("name") or parsed.netloc,url,utcnow()))
   (added if cur.rowcount else skipped).append(url if cur.rowcount else {"feed_url":url,"reason":"duplicate"})
  conn.commit()
 print(json.dumps({"added":added,"skipped":skipped},indent=2))
if __name__=="__main__": main()
