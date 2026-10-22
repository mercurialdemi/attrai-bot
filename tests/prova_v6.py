import asyncio, io, os, sys
T=sys.argv[1]; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"]=T+"/data"
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from bot import writer, images, instagram, pipeline, storage, config
brand=writer.load_brand()
W,H=1080,1350
B="assets/testo_bold.otf"
def base():
    im=Image.new("RGB",(W,H),(20,20,30)); d=ImageDraw.Draw(im)
    for y in range(H): d.line((0,y,W,y),fill=(20+y//40,20,30+y//30))   # sfondo sfumato
    d.ellipse((300,600,780,1080),fill=(210,160,60)); d.rectangle((480,700,600,1000),fill=(120,60,30))  # illustrazione
    return im
orig=base(); d=ImageDraw.Draw(orig); f=ImageFont.truetype(B,64)
for i,l in enumerate(["BUILD YOUR","FINANCIAL FREEDOM."]): 
    tw=d.textlength(l,font=f); d.text(((W-tw)/2,170+i*80),l,font=f,fill=(250,210,90))
d.text((830,1270),"@otherpage",font=ImageFont.truetype(B,28),fill="white")
def jpg(i): b=io.BytesIO(); i.save(b,"JPEG",quality=95); return b.getvalue()
class Blk:
    def __init__(s,inp): s.type="tool_use"; s.input=inp
def create(**k):
    n=k["tools"][0]["name"]
    if n=="slide_tradotte":
        out={"senso":"s","formato":"singolo","natura":"virale","slides":[{"numero":1,"testo_originale":"BUILD YOUR\nFINANCIAL FREEDOM.",
            "testo":"COSTRUISCI LA TUA\nLIBERTÀ ECONOMICA.","watermark_originale":"@otherpage in basso a destra","e_cta":False,"da_modificare":True,
            "blocchi":[{"testo":"COSTRUISCI LA TUA\nLIBERTÀ ECONOMICA.","x":180,"y":115,"w":640,"h":130,"colore":"#FFD060","allineamento":"center","peso":"bold","maiuscolo":True}]}]}
    elif n=="descrizione_pronta":
        out={"descrizione":"D","tipo_hook":"frase secca","autori_citati":[],"cta":{"tipo":"segui","manuale":"","frase":"x","motivo":"m"}}
    else: out={"testo_corretto":True,"residui":False,"problemi":""}
    return type("R",(),{"content":[Blk(out)],"stop_reason":"tool_use"})()
writer.client=lambda: type("C",(),{"messages":type("M",(),{"create":staticmethod(create)})})()
prompts=[]
def gem(o,p): prompts.append(p[:60]); return base().resize((896,1120))   # Gemini "pulito": stessa immagine senza testo
images._gemini_edit=gem
src=instagram.SourcePost(url="https://www.instagram.com/p/G/",owner="collab",caption="c",kind="Image")
post,paths,failed=asyncio.run(pipeline.build_post(src,brand,"g",originals=[jpg(orig)]))
print("prompt gemini:",prompts, "| failed:",failed, "| canva:", (post["slides"][0].get("_canva") or {}).get("tipo"), "| avviso:", post.get("_avviso","")[:50])
c=Image.new("RGB",(W*2+20,H),"gray"); c.paste(orig,(0,0)); c.paste(Image.open(paths[0]),(W+20,0)); c.resize((1090,675)).save(T+"/g.jpg")
