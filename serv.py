import json
import asyncio
import FourgTV
import uvicorn
from fastapi import FastAPI, Response

app = FastAPI()
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

@app.get("/4gtv/tags")
async def fgtv_tags():
    return Response(content=await four.get_channel_sets(), status_code=200)

@app.get("/4gtv/tag/{setid}")
async def fgtv_tag(setid: int):
    return Response(content=await four.get_channel_by_set_id(setid), status_code=200)

@app.get("/4gtv/epg/{chid}")
async def fgtv_epg(chid: str):
    return Response(content=await four.get_epg(chinfo[chid]["fs4GTV_ID"]), status_code=200)

@app.get("/4gtv/raw/{chid}")
async def fgtv_raw(chid: str):
    return Response(content=await four.get_channel_url3(chid, chinfo[chid]["fs4GTV_ID"]), status_code=200)

@app.get("/4gtv/hls/{chid}")
async def fgtv_hls(chid: str):
    return Response(content=await four.get_hls(chid, chinfo[chid]["fs4GTV_ID"]), status_code=200)

if __name__ == "__main__":
    with open("config.json", "r", encoding='utf8') as conf:
        conf = json.load(conf)
    with open("channels.json", "r", encoding="utf8") as channels:
        chinfo = {str(ch["fnID"]): ch for ch in json.load(channels)["Data"]}
    four = FourgTV.FourgTV(conf=conf["4gtv"])
    uvicorn.run(app, host=conf["server"]["bind"], port=conf["server"]["port"], 
                timeout_keep_alive=10, log_level="debug")
