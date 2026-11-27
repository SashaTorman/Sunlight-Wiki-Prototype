#!/usr/bin/env python3
"""Refresh the prototype's five-product selections from public SUNLIGHT pages.

Run from any directory: python3 scripts/update-catalog-products.py
No API key or runtime cross-origin requests are required by the prototype.
"""
import concurrent.futures
import datetime
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://sunlight.net"
GROUPS = {
    "jewelry": (None, None, "Украшения"),
    "chains": ("product_type", "Цепи", "Цепи"),
    "watches": ("product_type", "Часы", "Часы"),
    "okami": ("brand", "OKAMI", "Часы OKAMI"),
    "rings": ("product_type", "Кольца", "Кольца"),
    "earrings": ("product_type", "Серьги", "Серьги"),
    "bracelets": ("product_type", "Браслеты", "Браслеты"),
    "pendants": ("product_type", "Подвески", "Подвески"),
    "necklaces": ("product_type", "Колье", "Колье"),
    "brooches": ("product_type", "Брошь", "Броши"),
    "gold": ("material", "Золото", "Украшения из золота"),
    "silver": ("material", "Серебро", "Украшения из серебра"),
    "platinum": ("material", "Платина", "Украшения из платины"),
    "diamonds": ("stone", "Бриллиант природный", "Украшения с бриллиантами"),
    "pearl": ("stone", "Жемчуг культивированный", "Украшения с жемчугом"),
    "aquamarine": ("stone", "Аквамарин природный", "Украшения с аквамарином"),
    "jade": ("stone", "Нефрит", "Украшения с нефритом"),
    "garnet": ("stone", "Гранат природный", "Украшения с гранатом"),
    "amber": ("stone", "Янтарь природный", "Украшения с янтарём"),
    "sapphire": ("stone", "Сапфир облагороженный", "Украшения с сапфирами"),
    "emerald": ("stone", "Изумруд облагороженный", "Украшения с изумрудами"),
    "ruby": ("stone", "Рубин облагороженный", "Украшения с рубинами"),
    "topaz": ("stone", "Топаз природный", "Украшения с топазами"),
    "amethyst": ("stone", "Аметист природный", "Украшения с аметистами"),
    "agate": ("stone", "Агат природный", "Украшения с агатом"),
    "onyx": ("stone", "Оникс природный", "Украшения с ониксом"),
    "quartz": ("stone", "Кварц природный", "Украшения с кварцем"),
    "turquoise": ("stone", "Бирюза", "Украшения с бирюзой"),
    "opal": ("stone", "Опал природный", "Украшения с опалом"),
    "citrine": ("stone", "Цитрин природный", "Украшения с цитрином"),
    "peridot": ("stone", "Хризолит природный", "Украшения с хризолитом"),
    "tourmaline": ("stone", "Турмалин природный", "Украшения с турмалином"),
}


def download(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not (
        parsed.hostname == "sunlight.net" or parsed.hostname.endswith(".sunlight.net")
    ):
        raise ValueError("Only public SUNLIGHT URLs are supported")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()


def catalog(path):
    html = download(ORIGIN + path).decode("utf-8")
    match = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("Catalog data missing: " + path)
    refs = json.loads(match.group(1))
    memo = {}

    def resolve(index):
        if index < 0:
            return None
        if index in memo:
            return memo[index]
        value = refs[index]
        if isinstance(value, dict):
            result = {}
            memo[index] = result
            result.update({key: resolve(ref) for key, ref in value.items()})
            return result
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                return resolve(value[1]) if len(value) > 1 else None
            result = []
            memo[index] = result
            result.extend(resolve(ref) for ref in value)
            return result
        return value

    data = resolve(0)["data"]
    return next(value for key, value in data.items() if "/api/catalog/page/" in key)


def selection(key, settings, filters, base):
    field, option, label = settings
    path = filters[field][option] if field else "/catalog/"
    data = catalog(path) if field else base
    products = []
    seen = set()
    for item in data["products"]:
        if not item.get("is_available") or not item.get("is_available_for_order"):
            continue
        if item["url"] in seen or not item.get("main_image"):
            continue
        seen.add(item["url"])
        products.append({
            "id": item["article"], "name": item["name"],
            "brand": item["brand_name"], "description": item["descr"],
            "url": ORIGIN + item["url"], "image": item["main_image"],
            "price": item["pricing"]["final_price"],
        })
        if len(products) == 5:
            break
    if len(products) != 5:
        print(f"{key}: selection omitted; only {len(products)} available products", flush=True)
        return key, None
    return key, {"label": label, "url": ORIGIN + path, "products": products}


def main():
    base = catalog("/catalog/")
    filters = {
        item["slug"]: {child["name"]: child["link"] for child in item["children"]}
        for item in base["filters"]
        if item["slug"] in {"product_type", "stone", "material", "brand"}
    }
    groups = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(selection, key, settings, filters, base)
                 for key, settings in GROUPS.items()]
        for task in concurrent.futures.as_completed(tasks):
            key, group = task.result()
            if group:
                groups[key] = group
                print(key + ": 5 products", flush=True)
    images = ROOT / "img" / "catalog"
    images.mkdir(parents=True, exist_ok=True)
    unique = {p["id"]: p for group in groups.values() for p in group["products"]}

    def save_image(item):
        target = images / (item["id"] + ".jpg")
        if not target.exists():
            image_url = urllib.parse.urlparse(item["image"])
            # Use the catalog CDN's thumbnail format instead of full-size photos.
            thumbnail = image_url._replace(path="/r/w540/q85" + image_url.path)
            target.write_bytes(download(urllib.parse.urlunparse(thumbnail)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save_image, unique.values()))
    for group in groups.values():
        for product in group["products"]:
            product["sourceImage"] = product["image"]
            product["image"] = "../img/catalog/" + product["id"] + ".jpg"
    output = {
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": ORIGIN + "/catalog/", "groups": dict(sorted(groups.items())),
    }
    destination = ROOT / "data" / "catalog-products.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved {len(groups)} selections and {len(unique)} images", flush=True)


if __name__ == "__main__":
    main()
