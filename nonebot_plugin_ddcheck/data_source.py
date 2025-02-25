import json
import httpx
import jinja2
import math
from pathlib import Path
from typing import List, Union
from nonebot import get_driver
from nonebot.log import logger
from nonebot_plugin_apscheduler import scheduler
from .HTP2 import html_to_pic


from .config import Config

dd_config = Config.parse_obj(get_driver().config.dict())

data_path = Path("data/ddcheck")
vtb_list_path = data_path / "vtb_list.json"

dir_path = Path(__file__).parent
template_path = dir_path / "template"
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(template_path), enable_async=True
)

proxies = { "http://": None, "https://": None}
async def update_vtb_list():
    vtb_list = []
    urls = [
        "https://api.vtbs.moe/v1/short",
        "https://api.tokyo.vtbs.moe/v1/short",
        "https://vtbs.musedash.moe/v1/short",
    ]
    async with httpx.AsyncClient(proxies=proxies) as client:
        for url in urls:
            try:
                resp = await client.get(url, timeout=20)
                result = resp.json()
                if not result:
                    continue
                for info in result:
                    if info.get("mid", None) and info.get("uname", None):
                        vtb_list.append(info)
                break
            except httpx.TimeoutException:
                logger.warning(f"Get {url} timeout")
    dump_vtb_list(vtb_list)


scheduler.add_job(
    update_vtb_list,
    "cron",
    hour=3,
    id="update_vtb_list",
)


def load_vtb_list() -> List[dict]:
    if vtb_list_path.exists():
        with vtb_list_path.open("r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.decoder.JSONDecodeError:
                logger.warning("vtb列表解析错误，将重新获取")
                vtb_list_path.unlink()
    return []


def dump_vtb_list(vtb_list: List[dict]):
    data_path.mkdir(parents=True, exist_ok=True)
    json.dump(
        vtb_list,
        vtb_list_path.open("w", encoding="utf-8"),
        indent=4,
        separators=(",", ": "),
        ensure_ascii=False,
    )


async def get_vtb_list() -> List[dict]:
    vtb_list = load_vtb_list()
    if not vtb_list:
        await update_vtb_list()
    return load_vtb_list()


async def get_uid_by_name(name: str) -> int:
    try:
        url = "http://api.bilibili.com/x/web-interface/search/type"
        params = {"search_type": "bili_user", "keyword": name}
        async with httpx.AsyncClient(proxies=proxies) as client:
            resp = await client.get(url, params=params, timeout=10)
            result = resp.json()
        for user in result["data"]["result"]:
            if user["uname"] == name:
                return user["mid"]
        return 0
    except (KeyError, IndexError, httpx.TimeoutException) as e:
        logger.warning(f"Error in get_uid_by_name({name}): {e}")
        return 0


async def get_user_info(uid: int) -> dict:
    try:
        url = "https://account.bilibili.com/api/member/getCardByMid"
        params = {"mid": uid}
        async with httpx.AsyncClient(proxies=proxies) as client:
            resp = await client.get(url, params=params, timeout=10)
            result = resp.json()
        return result["card"]
    except (KeyError, IndexError, httpx.TimeoutException) as e:
        logger.warning(f"Error in get_user_info({uid}): {e}")
        return {}


async def get_medals(uid: int) -> List[dict]:
    try:
        url = "https://api.live.bilibili.com/xlive/web-ucenter/user/MedalWall"
        params = {"target_id": uid}
        headers = {"cookie": dd_config.bilibili_cookie}
        async with httpx.AsyncClient(proxies=proxies) as client:
            resp = await client.get(url, params=params, headers=headers)
            result = resp.json()
        return result["data"]["list"]
    except (KeyError, IndexError, httpx.TimeoutException) as e:
        logger.warning(f"Error in get_medals({uid}): {e}")
        return []


def format_color(color: int) -> str:
    return f"#{color:06X}"


def format_vtb_info(info: dict, medal_dict: dict) -> dict:
    name = info["uname"]
    uid = info["mid"]
    medal = {}
    if name in medal_dict:
        medal_info = medal_dict[name]["medal_info"]
        medal = {
            "name": medal_info["medal_name"],
            "level": medal_info["level"],
            "color_border": format_color(medal_info["medal_color_border"]),
            "color_start": format_color(medal_info["medal_color_start"]),
            "color_end": format_color(medal_info["medal_color_end"]),
        }
    return {"name": name, "uid": uid, "medal": medal}


async def get_reply(name: str) -> Union[str, bytes]:
    if name.isdigit():
        uid = int(name)
    else:
        uid = await get_uid_by_name(name)
    user_info = await get_user_info(uid)
    if not user_info:
        return "获取用户信息失败，请检查名称或稍后再试"

    vtb_list = await get_vtb_list()
    if not vtb_list:
        return "获取vtb列表失败，请稍后再试"

    medals = await get_medals(uid)
    medal_dict = {medal["target_name"]: medal for medal in medals}

    vtb_dict = {info["mid"]: info for info in vtb_list}
    vtbs = [
        info for uid, info in vtb_dict.items() if uid in user_info.get("attentions", [])
    ]
    vtbs = [format_vtb_info(info, medal_dict) for info in vtbs]

    follows_num = int(user_info["attention"])
    vtbs_num = len(vtbs)
    percent = vtbs_num / follows_num * 100 if follows_num else 0
    result = {
        "name": user_info["name"],
        "uid": user_info["mid"],
        "face": user_info["face"],
        "fans": user_info["fans"],
        "follows": user_info["attention"],
        "percent": f"{percent:.2f}% ({vtbs_num}/{follows_num})",
        "vtbs": vtbs,
        "p": math.ceil(vtbs_num/math.ceil(vtbs_num/100))
    }
    template = env.get_template("info.html")
    content = await template.render_async(info=result)
    return await html_to_pic(content, wait=0, viewport={"width": 100, "height": 100})
