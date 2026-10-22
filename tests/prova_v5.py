import asyncio, io, os, sys
T=sys.argv[1]; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"]=T+"/data"
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from bot import writer, images, instagram, pipeline, storage
brand=writer.load_brand()
W,H=1080,1350
R="assets/testo_regular.otf"
def jpg(img): b=io.BytesIO(); img.save(b,"JPEG",quality=95); return b.getvalue()
# slide 1: testo inglese su blu + watermark altrui
a=Image.new("RGB",(W,H),(13,21,36)); d=ImageDraw.Draw(a); f=ImageFont.truetype(R,50); y=480
for ln in ["A 10-second reply doesn't require","\"free time.\"","","It requires interest.","Busy people still reply","to what they value."]:
    if ln: d.text((90,y),ln,font=f,fill=(235,235,240))
    y+=66 if ln else 40
d.text((820,1240),"@otherpage",font=ImageFont.truetype(R,30),fill=(235,235,240))
# slide 2: grafica (illustrazione al centro)
b_=Image.new("RGB",(W,H),(13,21,36)); d=ImageDraw.Draw(b_); d.rectangle((200,400,880,1000),fill=(200,150,60)); d.text((90,150),"Build your status.",font=f,fill="white")
# slide 3: nostra CTA, già italiano
c=Image.new("RGB",(W,H),(0,0,0)); ImageDraw.Draw(c).text((200,600),"Seguici @attraiseduci",font=f,fill="white")
orig=[jpg(a),jpg(b_),jpg(c)]
class Blk:
    def __init__(s,inp): s.type="tool_use"; s.input=inp
calls=[]
def create(**k):
    n=k["tools"][0]["name"]; calls.append(n)
    if n=="slide_tradotte":
        out={"senso":"s","formato":"carosello","natura":"didattico","slides":[
          {"numero":1,"testo_originale":"A 10-second reply doesn't require\n\"free time.\"\nIt requires interest.\nBusy people still reply\nto what they value.","testo":"Una risposta di 10 secondi non richiede\n\"tempo libero\".\n\nRichiede interesse.\nLe persone impegnate rispondono comunque\na ciò a cui danno valore.","watermark_originale":"@otherpage in basso a destra","da_modificare":True},
          {"numero":2,"testo_originale":"Build your status.","testo":"Costruisci il tuo status.","watermark_originale":"","da_modificare":True},
          {"numero":3,"testo_originale":"Seguici @attraiseduci","testo":"Seguici @attraiseduci","watermark_originale":"@attraiseduci","da_modificare":False}]}
    elif n=="descrizione_pronta":
        out={"descrizione":"Desc","tipo_hook":"frase secca","autori_citati":[],"cta":{"tipo":"segui","manuale":"","frase":"x","motivo":"m"}}
    else:
        out={"testo_corretto":True,"residui":False,"problemi":""}
    return type("R",(),{"content":[Blk(out)],"stop_reason":"tool_use"})()
writer.client=lambda: type("C",(),{"messages":type("M",(),{"create":staticmethod(create)})})()
gem=[]
def fake_gem(o,p):
    gem.append(1); img=o.copy(); return img
images._gemini_edit=fake_gem
src=instagram.SourcePost(url="https://www.instagram.com/p/Z/",owner="collab",caption="c",kind="Sidecar")
post,paths,failed=asyncio.run(pipeline.build_post(src,brand,"x",originals=orig))
print("gemini:",len(gem),"| chiamate claude:",calls,"| failed:",failed)
print("canva info:",[ (s.get("_canva") or {}).get("tipo") for s in post["slides"]])
sent=[]
class M:
    def __init__(s): s.message_id=len(sent)
class Bot:
    async def send_media_group(s,c,g): sent.append(("group",len(g))); return [M() for _ in g]
    async def send_document(s,c,data,filename,caption=None):
        sent.append(("doc",filename)); Path(T,filename).write_bytes(data); return M()
    async def send_message(s,c,t): sent.append(("msg",t[:40])); return M()
jid=storage.save_job(1,1,src.url,src.owner,{k:v for k,v in post.items()},paths)
asyncio.run(pipeline.send_post(Bot(),1,jid,post,paths,failed))
print(sent)
Image.open(paths[0]).resize((540,675)).save(T+"/s1.jpg")
