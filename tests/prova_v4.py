import asyncio, io, os, sys
T=sys.argv[1]; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"]=T+"/data"
from pathlib import Path
from PIL import Image
from bot import writer, images, config, instagram, pipeline, main, storage
brand=writer.load_brand()
class Blk:
    def __init__(s,inp): s.type="tool_use"; s.input=inp
checks=[]
def create(**k):
    n=k["tools"][0]["name"]
    if n=="slide_tradotte":
        out={"senso":"s","formato":"carosello","natura":"didattico","slides":[
          {"numero":1,"testo_originale":"Ciao","testo":"Ciao","watermark_originale":"","da_modificare":False},
          {"numero":2,"testo_originale":"Hi","testo":"Ciao","watermark_originale":"@other basso","da_modificare":True},
          {"numero":3,"testo_originale":"Seguici","testo":"Seguici","watermark_originale":"@attraiseduci in basso","da_modificare":False}]}
    elif n=="descrizione_pronta":
        out={"descrizione":"D","tipo_hook":"frase secca","autori_citati":[],"cta":{"tipo":"segui","manuale":"","frase":"x","motivo":"m"}}
    else:
        checks.append(len([c for c in k["messages"][0]["content"] if c.get("type")=="image"]))
        out={"testo_corretto":True,"residui":False,"problemi":""}
    return type("R",(),{"content":[Blk(out)],"stop_reason":"tool_use"})()
writer.client=lambda: type("C",(),{"messages":type("M",(),{"create":staticmethod(create)})})()
gem=[]
images._gemini_edit=lambda o,p: (gem.append(1), Image.new("RGB",(896,1152),(40,40,40)))[1]
def jp(c):
    b=io.BytesIO(); Image.new("RGB",(1080,1350),c).save(b,"JPEG"); return b.getvalue()
orig=[jp((10,10,10)),jp((20,20,20)),jp((30,30,30))]
src=instagram.SourcePost(url="https://www.instagram.com/p/X/",owner="collab",caption="c",kind="Sidecar")
st=[]
async def s(t): st.append(t)
post,paths,failed=asyncio.run(pipeline.build_post(src,brand,"t",on_status=s,originals=orig))
print("gemini calls:",len(gem),"| immagini per controllo:",checks,"| failed:",failed)
print("slide1 identica all'originale:", paths[0].read_bytes()==orig[0], "| slide3 identica:", paths[2].read_bytes()==orig[2])
print("watermark slide3 azzerato:", repr(post["slides"][2]["watermark_originale"]), "| status:", st[-1])
# post della nostra pagina: niente Gemini
gem.clear(); src2=instagram.SourcePost(url="https://www.instagram.com/p/Y/",owner="attraiseduci",caption="c",kind="Sidecar")
post2,paths2,_=asyncio.run(pipeline.build_post(src2,brand,"u",originals=orig))
print("post nostro -> gemini calls:",len(gem), all(p.read_bytes()==o for p,o in zip(paths2,orig)))
# restricted
class DS:
    def iterate_items(self): return iter([{"error":"restricted_page","errorDescription":"Restricted"}])
class AC:
    def call(self,**k): return {"defaultDatasetId":"d"}
class AP:
    def __init__(s,t): pass
    def actor(s,a): return AC()
    def dataset(s,d): return DS()
instagram.ApifyClient=AP
try: instagram.fetch_post("u")
except instagram.InstagramError as e: print("msg:",str(e)[:70])
