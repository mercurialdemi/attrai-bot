import asyncio, io, os, sys, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
T=sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"]=T+"/data"
from PIL import Image, ImageDraw
from bot import config, writer, images, pipeline, storage, report, analysis, instagram, main
brand = writer.load_brand()

# ---- finto Claude: risponde in base allo strumento richiesto
calls=[]
check_results=[]
class Blk:
    def __init__(s, inp): s.type="tool_use"; s.input=inp
def fake_create(**k):
    name=k["tools"][0]["name"]; calls.append((name, k["model"]))
    if name=="slide_tradotte":
        out={"senso":"s","formato":"carosello","natura":"didattico","slides":[
            {"numero":1,"testo_originale":"Hi","testo":"Ciao — **mondo**","watermark_originale":"@other basso destra"},
            {"numero":2,"testo_originale":"Bye","testo":"Addio","watermark_originale":""}]}
        assert "SIGNIFICATO" in (k["system"] if isinstance(k["system"], str) else k["system"][0]["text"])
    elif name=="descrizione_pronta":
        out={"descrizione":"Desc","tipo_hook":"verità brutale","autori_citati":["Buss"],"cta":{"tipo":"manuale","manuale":"friendzone","frase":"x","motivo":"m"}}
        assert "GUIDA DI STILE" in (k["system"] if isinstance(k["system"], str) else k["system"][0]["text"])
    elif name=="esito_controllo":
        out=check_results.pop(0) if check_results else {"testo_corretto":True,"residui":False,"problemi":""}
    elif name=="studio_pagina":
        out={"guida":"- apri con frase secca","dolori":["friendzone"],"da_evitare":["citazioni all'inizio"],"esempi":[1,2,99]}
    elif name=="seleziona_post":
        out={"scelti":[{"numero":1,"motivo":"forte"}]}
    return type("R",(),{"content":[Blk(out)],"stop_reason":"tool_use"})()
writer.client=lambda: type("C",(),{"messages":type("M",(),{"create":staticmethod(fake_create)})})()

# ---- analisi
now=datetime.now(timezone.utc)
def P(owner, code, likes, comments, hours, cap="x"*200, video=False):
    return instagram.SourcePost(url=f"https://www.instagram.com/p/{code}/", owner=owner, caption=cap, kind="Video" if video else "Sidecar",
        image_urls=["u"], likes=likes, comments=comments, timestamp=(now-timedelta(hours=hours)).isoformat())
instagram.fetch_profiles=lambda h,n,*_a: [P("attraiseduci",f"a{i}",100*i,i,500+i, cap=f"Didascalia numero {i} "+"y"*150) for i in range(30)]
st=analysis.run_analysis(brand); print("analisi:", st["post_analizzati"], len(st["esempi"]), st["guida"].splitlines()[0])

# ---- create_post: due chiamate separate
post=writer.create_post("cap","other",[b"x"],brand,[],{"hook":[],"autori":[]}) if False else None
img=Image.new("RGB",(1080,1350),(20,20,20)); b=io.BytesIO(); img.save(b,"JPEG"); ob=b.getvalue()
post=writer.create_post("cap","other",[ob,ob],brand,[],{"hook":[],"autori":[]})
print("chiamate:", [c[0] for c in calls[-2:]], "| testo slide:", repr(post["slides"][0]["testo"]))

# ---- render: logo solo se c'era watermark; controllo che fallisce
images._gemini_edit=lambda o,p: Image.new("RGB",(896,1152),(40,40,40))
config.SLIDE_CHECK=True
check_results[:]=[{"testo_corretto":False,"residui":False,"problemi":"typo"},{"testo_corretto":True,"residui":False,"problemi":""}]
i1,ok1,_=images.render_slide(ob, post["slides"][0], brand, "gemini")
i2,ok2,_=images.render_slide(ob, post["slides"][1], brand, "gemini")
def has_logo(im): return im.getpixel((1000, 1290)) != (40,40,40)
print("slide1 ok:",ok1,"logo:",has_logo(i1)," | slide2 ok:",ok2,"logo:",has_logo(i2))
check_results[:]=[{"testo_corretto":False,"residui":True,"problemi":"old watermark"}]*3
i3,ok3,pr=images.render_slide(ob, post["slides"][0], brand, "gemini"); print("sempre sbagliata -> ok:",ok3, pr)

# ---- invio: le slide fallite non partono
sent=[]
class Bot:
    async def send_media_group(s,c,g): sent.append(("group",[x.filename for x in g])); return [type("M",(),{"message_id":len(sent)})()]
    async def send_document(s,c,d,filename,caption=None): sent.append(("doc",filename)); return type("M",(),{"message_id":len(sent)})()
    async def send_photo(s,c,d,caption=None): sent.append(("foto",caption[:40])); return type("M",(),{"message_id":len(sent)})()
    async def send_message(s,c,t,**kw): sent.append(("msg",t[:60])); return type("M",(),{"message_id":len(sent)})()
p1=Path(T)/"a.jpg"; i1.save(p1); p2=Path(T)/"b.jpg"; i2.save(p2)
asyncio.run(pipeline.send_post(Bot(),1,7,post,[p1,p2],[2]))
for x in sent: print("  ", x)
print("riprova:", bool(main.RETRY_RE.match("Riprova")), bool(main.RETRY_RE.match("riprova la slide 3 più corta")))

# ---- report con storico
posts=[P("big",f"b{i}",10000,100,200+i) for i in range(10)]+[P("big","bNEW",9000,90,5),P("big","bNEW2",30000,300,6)]
c=report.rank_candidates(posts,48,15); print("candidati:",[(x.url.split('/')[-2],round(x.score,2)) for x in c])
# simula storico: 10 post vecchi più deboli -> media storica più bassa
storage.save_post_stats([(f"h{i}","big",2000.0,(now-timedelta(days=10+i)).isoformat()) for i in range(10)])
c=report.rank_candidates(posts,48,15); print("con storico:",[(x.url.split('/')[-2],round(x.score,2)) for x in c])
sent.clear()
instagram.fetch_profiles=lambda h,n,*_a: posts
async def fb(src,brand,suffix,on_status=None): return {"descrizione":"d","cta":{},"slides":[]},[p1],[]
pipeline.build_post=fb
async def fs(bot,c,job_id,post,paths,failed,only_text=False,header="",numbers=None):
    storage.save_job(0,0,"dummy","x",post,paths); sent.append(("POST",header.splitlines()[0]))
pipeline.send_post=fs
import yaml
asyncio.run(report.run_report(Bot(),[1]))
print("solo link:", [x[1] for x in sent])
assert any("bNEW2" in str(x) or "sopra la media" in str(x) or "post dei collaboratori" in str(x) for x in sent), "report solo link vuoto"
sent.clear(); instagram._cache.clear()   # i post finti hanno un proprietario fuori elenco: niente lettura del giorno
asyncio.run(report.run_report(Bot(),[1]))                 # stesso giorno: niente post già mandati
assert not any(("sopra la media" in str(x) or "post dei collaboratori" in str(x)) for x in sent), "il report ripropone post già mandati"
import json as _j                                         # se nel frattempo raddoppia, torna come "in crescita"
_d=_j.loads(report._shown_file().read_text()); _d={u:[v[0],0.5] for u,v in _d.items()}; report._shown_file().write_text(_j.dumps(_d))
sent.clear(); instagram._cache.clear(); asyncio.run(report.run_report(Bot(),[1]))
assert any(("sopra la media" in str(x) or "post dei collaboratori" in str(x)) for x in sent), "post esploso dopo non ripreso"
report._shown_file().unlink()
sent.clear()
_ld=writer.load_brand
writer.load_brand=lambda: {**_ld(), "report": {**_ld().get("report", {}), "prepara_post": True}}
asyncio.run(report.run_report(Bot(),[1]))
print([x for x in sent if x[0]=="POST"], "| sotto media marcati:", any("sotto media" in str(x) for x in sent))
sent.clear(); asyncio.run(report.run_more(Bot(),[1])); print("altri:", [x[1] for x in sent])
sent.clear(); asyncio.run(report.run_more(Bot(),[1])); print("altri 2:", [x[1] for x in sent])
