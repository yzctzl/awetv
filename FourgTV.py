import math
import re
import json
import time
import copy
import logging
import urllib.parse
from base64 import b64decode, b64encode

import m3u8
import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from tenacity import retry, stop_after_attempt


logger = logging.getLogger(__name__)
# logging.basicConfig(format="%(levelname)s:     %(message)s", level=logging.INFO)

class FourgTV():
    def __init__(self, conf: dict) -> None:
        self.conf = conf

        self.host = "https://www.4gtv.tv"
        self.api2 = "https://api2.4gtv.tv"
        self.headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:103.0) Gecko/20100101 Firefox/103.0",
            "referer": "https://www.4gtv.tv",
            "cookie": f"4fourTV={conf['login']['4fourgtv']}"
        }
        self.proxy = conf["conn"]["proxy"]
        self.proxys = {
            "http": conf["conn"]["proxy"],
            "https": conf["conn"]["proxy"]
        }

        self.key = b"ilyB29ZdruuQjC45JhBBR7o2Z8WJ26Vg"
        self.iv  = b"JUMxvVMmszqUTeKn"

        self.streams = {}

    def url3_encrypt(self, body: dict) -> str:
        value = json.dumps(body).encode("utf8")
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        ct_bytes = cipher.encrypt(pad(value, AES.block_size))
        return b64encode(ct_bytes).decode('utf8')

    def url3_decrypt(self, data: str) -> bytes:
        ct_bytes = b64decode(data.encode("utf8"))
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        pt_bytes = cipher.decrypt(ct_bytes)
        return unpad(pt_bytes, AES.block_size)

    @retry(stop=stop_after_attempt(3))
    async def aget(self, uri: str) -> bytes:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(uri, proxy = self.proxy) as resp:
                resp.raise_for_status()
                return await resp.read()

    @retry(stop=stop_after_attempt(3))
    async def apost(self, uri: str, body: dict) -> bytes:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(uri, proxy = self.proxy, json=body) as resp:
                resp.raise_for_status()
                return await resp.read()

    async def get_channel_sets(self) -> bytes:
        return await self.aget(self.api2 + "/Channel/GetChannelSet/pc/L")

    async def get_channel_by_set_id(self, setid: int) -> bytes:
        return await self.aget(self.api2 + f"/Channel/GetChannelBySetId/{setid}/pc/L")

    async def get_channel_url3(self, chid: str, asid: str) -> bytes:
        body = {"fnCHANNEL_ID": chid, "fsASSET_ID": asid, "fsDEVICE_TYPE":"pc",
                "clsIDENTITY_VALIDATE_ARUS":{"fsVALUE":""}}
        content = await self.apost(self.api2 + "/Channel/GetChannelUrl3", {"value": self.url3_encrypt(body)})
        return self.url3_decrypt(json.loads(content)["Data"])

    def stream_expire(self, url: str) -> int:
        query = urllib.parse.parse_qs(url)
        if "expires" in query:
            return int(query["expires"][0])
        else:
            return int(query["expires1"][0])

    def up_stream(self, stream: m3u8.M3U8) -> m3u8.M3U8:
        for segment in stream.segments:
            url = segment.uri
            if "4gtvfree-cds" in url:
                url = url.replace("4gtvfree-cds", "4gtv-cds")
            elif "4gtvfreepc-cds" in url:
                url = url.replace("4gtvfreepc", "4gtvpc")
                url = url.replace("4gtv-live-mid", "4gtv-live-high")
            segment.uri = url
        return stream

    def generate_ts(self, now: int, stream_info: dict) -> m3u8.M3U8:
        stream : m3u8.M3U8 = copy.deepcopy(stream_info["point"]["stream"])
        seq_interval = round((now - stream_info["point"]["time"]) / stream.segments[0].duration)
        logger.debug(f"{seq_interval} - {stream.target_duration} - {stream.segments[0].duration}")
        start_seq = seq_interval + stream.media_sequence

        match0 = re.search(r"begin=(\d+)", stream.segments[0].uri)
        match1 = re.search(r"begin=(\d+)", stream.segments[1].uri)
        process_begin = match0 and match1
        if process_begin:
            begin = int(match0.groups()[0])
            interval = int(match1.groups()[0]) - begin
            logger.debug(f'{now} - {stream_info["point"]["time"]} - {stream.media_sequence} - {start_seq} - {begin} - {interval}')

        for i in range(len(stream.segments)):
            stream.segments[i].uri = stream.segments[i].uri.replace(
                f"{stream.media_sequence + i}.ts", f"{start_seq + i}.ts")
            if process_begin:
                stream.segments[i].uri = stream.segments[i].uri.replace(
                    f"begin={begin + i*interval}", f"begin={begin + (seq_interval+i)*interval}")
                logger.debug(stream.segments[i].uri + f"begin={begin + i*interval}" + f"begin={begin + (seq_interval+i)*interval}")
        stream.media_sequence = start_seq
        return stream

    async def get_stream_m3u8(self, chid: str, asid: str, quality: int = 0) -> str:
        result = json.loads(await self.get_channel_url3(chid, asid))
        index_m3u8_uri = result["flstURLs"][1]
        quality = quality if quality else len(result["flstBITRATE"]) - 1
        index_content = await self.aget(index_m3u8_uri)
        index = m3u8.loads(index_content.decode("utf8"), index_m3u8_uri)
        stream_uri = index.playlists[-1].absolute_uri.replace("/stream2", f"/stream{quality}")
        self.streams[chid] = { "uri": stream_uri, "expire": self.stream_expire(stream_uri)}
        return self.streams[chid]["uri"]

    async def get_hls(self, chid: str, asid: str, quality: int = 0) -> str:
        now = time.time()
        if chid in self.streams and "point" in self.streams[chid]:
            if now - self.streams[chid]["point"]["time"] < self.conf["sync"]["period"]:
                # return self.generate_ts(now, self.streams[chid]).dumps()
                stream_m3u8_uri = self.streams[chid]["uri"]
            else:
                if self.streams[chid]["expire"] - time.time() > 300:
                    stream_m3u8_uri = self.streams[chid]["uri"]
                else:
                    stream_m3u8_uri = await self.get_stream_m3u8(chid, asid, quality)
        else:
            stream_m3u8_uri = await self.get_stream_m3u8(chid, asid, quality)
        index_content = await self.aget(stream_m3u8_uri)
        stream = m3u8.loads(index_content.decode("utf8"), stream_m3u8_uri)
        stream = self.up_stream(stream)
        self.streams[chid]["point"] = {"time": now, "stream": stream}
        return stream.dumps()

    async def get_epg(self, asid: str) -> bytes:
        return await self.aget(self.host + f"/proglist/{asid}.txt")
