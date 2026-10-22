import asyncio, io, os, sys
T=sys.argv[1]; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"]=T+"/data"
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from bot import writer, images, instagram, pipeline
brand=writer.load_brand()
W,H=1080,1350
rng=np.random.default_rng(1)
photo=(rng.random((860,W,3))*80+np.linspace(60,160,W)[None,:,None]).astype(np.uint8)
orig=Image.new("RGB",(W,H),(0,0,0)); orig.paste(Image.fromarray(photo),(0,0))
d=ImageDraw.Draw(orig); A=ImageFont.truetype("assets/titolo_anton.ttf",78)
lines=[[("ELON MUSK'S MOTHER RAISED",0)],[("THREE ",0),("KIDS WHO ALL BECAME",1)],[("MILLIONAIRES.",1),(" WHEN ASKED",0)],[("HOW, SHE SAID THIS:",0)]]
y=905
for ln in lines:
    tw=sum(d.textlength(t,font=A) for t,_ in ln); x=(W-tw)/2
    for t,h in ln:
        d.text((x,y),t,font=A,fill=(230,80,80) if h else (255,255,255)); x+=d.textlength(t,font=A)
    y+=82
d.text((330,1280),"It started with one rule at age...",font=ImageFont.truetype("assets/testo_regular.otf",28),fill="white")
def jpg(i): b=io.BytesIO(); i.save(b,"JPEG",quality=95); return b.getvalue()
class Blk:
    def __init__(s,inp): s.type="tool_use"; s.input=inp
calls=[]
def create(**k):
    n=k["tools"][0]["name"]; calls.append((n,k["model"]))
    if n=="slide_tradotte":
        out={"concetto":"c","senso":"s","formato":"singolo","natura":"virale","slides":[{"numero":1,"testo_originale":"ELON MUSK'S MOTHER...","testo":"x",
          "watermark_originale":"","e_cta":False,"da_modificare":True,"blocchi":[
          {"testo":"LA MADRE DI ELON MUSK HA CRESCIUTO TRE [[FIGLI DIVENTATI TUTTI MILIONARI.]] QUANDO LE HANNO CHIESTO COME, HA RISPOSTO COSÌ:","x":60,"y":665,"w":880,"h":245,"colore":"#FFFFFF","allineamento":"center","peso":"bold","maiuscolo":True,"carattere":"titolo","colore_evidenziato":"#E65050"},
          {"testo":"Tutto è iniziato con una regola, a un'età precisa…","x":300,"y":945,"w":400,"h":30,"colore":"#FFFFFF","allineamento":"center","peso":"regular","maiuscolo":False,"carattere":"normale","colore_evidenziato":""}]}]}
    elif n=="slide_rilette":
        out={"slides":[{"numero":1,"blocchi":["LA MADRE DI ELON MUSK HA CRESCIUTO TRE [[FIGLI, TUTTI MILIONARI.]] COME HA FATTO? LEI HA RISPOSTO COSÌ:","Tutto è partito da una regola, a un'età precisa…"]}]}
    elif n=="descrizione_pronta":
        out={"descrizione":"D","tipo_hook":"frase secca","autori_citati":[],"cta":{"tipo":"segui","manuale":"","frase":"x","motivo":"m"}}
    else: out={"testo_corretto":True,"residui":False,"problemi":""}
    return type("R",(),{"content":[Blk(out)],"stop_reason":"tool_use"})()
writer.client=lambda: type("C",(),{"messages":type("M",(),{"create":staticmethod(create)})})()
gem=[]
images._gemini_edit=lambda o,p: gem.append(p) or None
src=instagram.SourcePost(url="https://www.instagram.com/p/M/",owner="collab",caption="c",kind="Image")
post,paths,failed=asyncio.run(pipeline.build_post(src,brand,"m",originals=[jpg(orig)]))
print("chiamate:",calls,"| gemini:",len(gem),"| failed:",failed)
print("testo slide:",post["slides"][0]["testo"])
c=Image.new("RGB",(W*2+20,H),"gray"); c.paste(orig,(0,0)); c.paste(Image.open(paths[0]),(W+20,0)); c.crop((0,850,W*2+20,H)).save(T+"/m.jpg")
