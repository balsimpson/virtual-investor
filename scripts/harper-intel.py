#!/usr/bin/env python3
"""Zero-agent RSS sweep: fetch, deduplicate, relevance-score, store or stage."""
from __future__ import annotations
import argparse, html, os, re, sqlite3, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
from harper_intel_core import PASS_THRESHOLD, insert_article, load_patterns, score_article, update_source_relevance, utcnow, fingerprint

DB = Path(os.environ.get("VIRTUAL_INVESTOR_DB", Path.home()/".hermes/data/virtual-investor/portfolio.db"))

def text(node, names):
    for child in node.iter():
        if child.tag.split('}')[-1].lower() in names and child.text:
            return child.text.strip()
    return ""

def entries(blob: bytes):
    root = ET.fromstring(blob)
    nodes = [n for n in root.iter() if n.tag.split('}')[-1].lower() in ("item","entry")]
    for n in nodes:
        title = html.unescape(text(n,{"title"}))
        link = text(n,{"link"})
        if not link:
            for c in n.iter():
                if c.tag.split('}')[-1].lower()=="link" and c.attrib.get("href"):
                    link=c.attrib["href"]; break
        summary = html.unescape(re.sub(r"<[^>]+>"," ",text(n,{"description","summary","content"})))
        if title and link: yield title,link,re.sub(r"\s+"," ",summary).strip()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",type=Path,default=DB); ap.add_argument("--timeout",type=int,default=20); ap.add_argument("--source-id",type=int); args=ap.parse_args()
    stats={"sources":0,"fetched":0,"stored":0,"staged":0,"duplicates":0,"errors":[]}
    with sqlite3.connect(args.db) as conn:
        conn.row_factory=sqlite3.Row
        patterns=load_patterns(conn)
        sql="SELECT * FROM intel_sources WHERE enabled=1"+(" AND id=?" if args.source_id else "")
        sources=conn.execute(sql,(args.source_id,) if args.source_id else ()).fetchall()
        for src in sources:
            stats["sources"]+=1
            try:
                req=urllib.request.Request(src["feed_url"],headers={"User-Agent":"HarperIntel/1.0"})
                with urllib.request.urlopen(req,timeout=args.timeout) as resp: blob=resp.read(2_000_000)
                fetched=unique=dups=tickers_count=0
                for title,link,summary in entries(blob):
                    fetched+=1; stats["fetched"]+=1
                    fp=fingerprint(link,title)
                    if conn.execute("SELECT 1 FROM intel_articles WHERE fingerprint=?",(fp,)).fetchone() or conn.execute("SELECT 1 FROM intel_relevance_staging WHERE link=?",(link,)).fetchone():
                        dups+=1; stats["duplicates"]+=1; continue
                    rel=score_article(f"{title}\n{summary}",patterns)
                    for pid in rel.matched_pattern_ids:
                        conn.execute("UPDATE intel_relevance_patterns SET match_count=match_count+1,last_matched_at=? WHERE id=?",(utcnow(),pid))
                    if rel.score>=PASS_THRESHOLD:
                        if insert_article(conn,src["id"],title,link,summary,rel.tickers): unique+=1; stats["stored"]+=1; tickers_count+=len(rel.tickers)
                        update_source_relevance(conn,src["id"],True)
                    else:
                        conn.execute("INSERT INTO intel_relevance_staging(source_id,title,link,summary,staged_at) VALUES(?,?,?,?,?)",(src["id"],title,link,summary,utcnow()))
                        stats["staged"]+=1
                conn.execute("UPDATE intel_sources SET last_fetch_at=?,total_fetched=total_fetched+?,unique_count=unique_count+?,duplicate_count=duplicate_count+?,ticker_mentions=ticker_mentions+? WHERE id=?",(utcnow(),fetched,unique,dups,tickers_count,src["id"]))
                quality = conn.execute("SELECT total_fetched,duplicate_count FROM intel_sources WHERE id=?", (src["id"],)).fetchone()
                if quality and int(quality[0] or 0) >= 50 and (int(quality[1] or 0) / max(int(quality[0] or 0), 1)) > 0.90:
                    conn.execute("UPDATE intel_sources SET enabled=0,reason_disabled=? WHERE id=?", (f"auto-disabled: duplicate rate {int(quality[1]) / int(quality[0]):.1%} after {int(quality[0])} fetches", src["id"]))
                conn.commit()
            except Exception as exc:
                stats["errors"].append({"source_id":src["id"],"error":str(exc)[:300]})
    import json; print(json.dumps(stats,indent=2))
if __name__=="__main__": main()
