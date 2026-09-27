#!/usr/bin/env python3
"""Download the openly licensed real-world files the evals run on.

    fetch_data.py [--out evals/results/data/downloads]

Everything comes from Wikimedia Commons (licences in data-sources.md).
Nothing here is committed; the folder is git-ignored. Re-run to refetch.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "dish-media-skill-eval/1.0 (https://github.com/maksymskuibida/claude-plugins; eval data fetch)"

HK = ("HK 上環 Sheung Wan 急庇利街 Clevely Street 鋿晶館 SC Cuisine Chinese Seafood Restaurant food "
      "乳豬全體 baby Suckling pig meat full dish siu yuk October 2025 N13P {:02d}.jpg")
PHOTOS = {  # local name -> Commons file name (without "File:")
    **{f"hk/N13P_{i:02d}.jpg": HK.format(i) for i in range(1, 12)},
    "mixed/khmer_01.jpg": "Khmer Family Restaurant, 2018 (01).jpg",
    "mixed/khmer_02.jpg": "Khmer Family Restaurant, 2018 (02).jpg",
    "mixed/khmer_03.jpg": "Khmer Family Restaurant, 2018 (03).jpg",
    "mixed/khmer_04.jpg": "Khmer Family Restaurant, 2018 (04).jpg",
    "mixed/turkey_starter.jpg": "Starter dish in Turkey.jpg",
    "mixed/lazarus_grilled_fish.jpg": "Grilled fish, Lazaru's Restaurant, New Urbane Hotel, 2025 (01).jpg",
    "mixed/meatloaf.jpg": "Dish of meatloaf served on a white plate with sauce and herbs in a restaurant setting.jpg",
}
# Single plated dishes from one photographer (PattayaPatrol, CC BY-SA 4.0), restaurant light, white plates:
# the closest thing on Commons to a real menu shoot with many different dishes.
PATTAYA = {
    "pattaya/sausages.jpg": "DFC 1093 Two grilled sausages served with creamy potato salad and a side of coleslaw on a white plate.jpg",
    "pattaya/spaghetti.jpg": "DFC 5021 A savory plate of spaghetti topped with crispy bacon grated cheese and a sprig of fresh parsley.jpg",
    "pattaya/skewers_fries.jpg": "DFC 1025 A plate of grilled sausage skewers with roasted vegetables golden fries and a side of coleslaw with dipping sauce.jpg",
    "pattaya/grilled_meat.jpg": "DFC 1238 Plate of seasoned grilled meat topped with raw onion rings served with a baked potato topped with creamy sauce a side of rice or cabbage and dipping sauce.jpg",
    "pattaya/skewers_tomato.jpg": "DFC 1493 Hearty plate of skewered grilled meat and veggies draped in a rich tomato sauce served with golden fries and a side of coleslaw.jpg",
    "pattaya/shrimp_rice.jpg": "DFC 5103 Shrimp fried rice served with lime sliced cucumber and a side of spicy chili sauce - a classic Thai comfort dish from Pattaya.jpg",
    "pattaya/steak_mash.jpg": "DFC 2288 Juicy grilled steak with creamy mashed potatoes and a colorful garden salad served with rich gravy on the side.jpg",
    "pattaya/steak_fries.jpg": "DFC 5113 Steak golden fries and fresh cabbage salad with dipping sauces - a classic comfort meal in Pattaya.jpg",
    "pattaya/salmon_shrimp.jpg": "DFC 1021 Seared salmon fillet topped with shrimp and dill served with coleslaw roasted potatoes and a side of creamed spinach on a bed of greens.jpg",
    "pattaya/club_sandwich.jpg": "DFC 4533 Triple-decker club juicy turkey melty cheddar crisp lettuce tomato served with fries and coleslaw.jpg",
    "pattaya/schnitzel.jpg": "DFC 3983 Crispy breaded schnitzel with lemon wedge golden fried potatoes creamy coleslaw and gravy on the side - a hearty comfort meal.jpg",
    "pattaya/salmon_sauce.jpg": "DZ6 1891 Seared salmon in a rich brown sauce with grilled potatoes fresh greens and toasted bread garnished with dill.jpg",
    "pattaya/papaya_salad.jpg": "DSCF0811 A vibrant plate of Thai green papaya salad with cherry tomatoes lime shellfish and squid in a tangy sauce.jpg",
    "pattaya/cashew_chicken.jpg": "DSCF0812 Savory cashew chicken stir-fry with bell peppers and tender pieces of meat glazed in a glossy sauce.jpg",
    "pattaya/octopus.jpg": "DFC 0313 Skewered grilled baby octopus glistening with a savory glaze and served on a bed of fresh greens.jpg",
}
# Unedited phone snapshots of restaurant meals (various uploaders, various lights): what a
# reviewer or an owner actually hands over, not a photographer's frame.
AMATEUR = {
    "amateur/nandos_chicken.jpg": "1-2 PERi-PERi Chicken + Reg Chips + Drink, Nando's Carousel Westfield, 2025 (01).jpg",
    "amateur/beef_burger.jpg": "Beef Burger, Inglewood Hotel, Western Australia, 2025 (01).jpg",
    "amateur/beinfleisch_wien.jpg": "Beinfleisch mit Senfkruste, Zucchini und Braterdäpfel - Restaurant Giorgina (Wien).jpg",
    "amateur/chicken_chips.jpg": "Chicken quarter and chips, Top Fryz, 2025 (01).jpg",
    "amateur/tokyo_italian_1.jpg": "Dish at Italian restaurant in Tokyo (78598).jpg",
    "amateur/tokyo_italian_2.jpg": "Dish at Italian restaurant in Tokyo.jpg",
    "amateur/enchilada.jpg": "Enchilada, Guzman y Gomez Carousel, 2025 (01).jpg",
    "amateur/zhongshan_dinner.jpg": "GD 廣東 Guangdong ZS 中山市 Zhongshan 古鎮鎮 Guzhen Town 鴻慶海鮮飯店 Hong Qing Seafood Restaurant 晚餐 diner September 2024 R12S 01.jpg",
    "amateur/goat_lisbon.jpg": "Goat served with pippies and chips at restaurant in Lisbon, Portugal (55117387607).jpg",
    "amateur/hk_fish_rice.jpg": "HK restaurant Cafe de Coral food 粟米魚栁飯 fish fillet with sweet corn sauce n rice October 2025 N13P 06.jpg",
    "amateur/schnitzel_majadra.jpg": "ILiat Portal for Foodie Disorder - Homemade schnitzel with majadra and vegetables.jpg",
    "amateur/rippa_box.jpg": "Large Rippa Box, Red Rooster Carousel FC, 2025 (01).jpg",
    "amateur/merida_dinner.jpg": "Micaela Mar & Leña Dinner, Mérida, Yucatán 2024.jpg",
    "amateur/nene_dosirak.jpg": "NeNe Dosirak Combo, NeNe Chicken Westfield Carousel, 2025 (01).jpg",
    "amateur/kway_teow.jpg": "One of Thai’s rich cultural food “Kway Teow”.jpg",
    "amateur/pizza_hut_pasta.jpg": "Oven-Baked Pasta, Pizza Hut Berlin Oranienburger Strasse, 2024 (01).jpg",
    "amateur/parmi.jpg": "Panko Crumbed Free Range Chicken Schnitzel Parmi, The Griffins Hotel, 2024 (01).jpg",
    "amateur/rawon.jpg": "Rawon, Kedai Juragan, BG Junction, 2025 (01).jpg",
    "amateur/roast_pork_katowice.jpg": "Roast pork and Silesian dumplings, Pod Siódemką, Katowice, 2024 (01).jpg",
    "amateur/shenzhen_dinner.jpg": "SZ 深圳 Shenzhen 福田 Futian 振興路 Zhenxing Road 味琪餐廳 Weiqi Restaurant 晚餐 dinner February 2024 R12S soup n red rice n roast duck n peanut on plate 01.jpg",
    "amateur/short_rib_biryani.jpg": "Short rib biryani, Oasis Hotel and Guest House, Voi, 2025 (01).jpg",
    "amateur/steak_sandwich.jpg": "Steak Sandwich, Albion Hotel, Cottesloe, 2025 (01).jpg",
    "amateur/zinger_banh_mi.jpg": "Zinger Bánh Mì Combo, KFC Garden City Food Court WA, 2025 (01).jpg",
    "amateur/cheesecake.jpg": "Baked New York Cheesecake, The Griffins Hotel, 2024 (01).jpg",
}
# Unedited phone videos of dishes in restaurants (handheld, no turntable): the other thing owners hand over.
AMATEUR_VIDEOS = {
    "video-amateur/double_cooked_pork.webm": ("Double-cooked pork, Xinjiang cuisine, Silk Road restaurant, London.webm", "1080p.vp9.webm"),
    "video-amateur/aubergine.webm": ("Home-style aubergine, Xinjiang cuisine, Silk Road restaurant, London.webm", "1080p.vp9.webm"),
    "video-amateur/wagyu.webm": ("2025-0118 Mujo 10 wagyu supplement.webm", "1080p.vp9.webm"),
    "video-amateur/teppanyaki.webm": ("Teppanyaki at Shima, Goodwood Park Hotel, Singapore - 20160417.webm", "1080p.vp9.webm"),
    "video-amateur/fast_food_seoul.webm": ("Fast food in Seoul.webm", "720p.vp9.webm"),
    "video-amateur/plov.webm": ("Cooking Plov.webm", "1080p.vp9.webm"),
    "video-amateur/brochettes_lyon.webm": ("Préparation des brochettes dans un restaurant japonais à Lyon.webm", "1080p.vp9.webm"),
}
VIDEOS = {  # local name -> (Commons file name, transcode key)
    "video/moon_turntable.webm": ("Moon Essentials- Turntable (SVS5319).webm", "1080p.vp9.webm"),
    "video/swift_360.webm": ("Swift Spacecraft Animations- 2025 (SVS14786 - SWIFT 360 4k 60fps, cropped).webm", "1080p.vp9.webm"),
    "video/al92_rotation.webm": ("Exposed Central Rotation of AL92 (CIRA 2024-06-21 - nolabels).webm", "1080p.vp9.webm"),
}


def commons_url(name: str, transcode: str | None = None) -> str:
    n = name.replace(" ", "_")
    m = hashlib.md5(n.encode("utf-8")).hexdigest()
    q = urllib.parse.quote(n)
    if transcode:
        return f"https://upload.wikimedia.org/wikipedia/commons/transcoded/{m[0]}/{m[:2]}/{q}/{q}.{transcode}"
    return f"https://upload.wikimedia.org/wikipedia/commons/{m[0]}/{m[:2]}/{q}"


def fetch(url: str, dst: Path) -> None:
    """One file, politely: a pause between files and a long back-off on 429."""
    import time
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size > 0:
        print(f"  have {dst.name}")
        return
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r, open(dst, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            print(f"  {dst.name}: {dst.stat().st_size // 1024} KB")
            time.sleep(2.0)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                wait = 45 * (attempt + 1)
                print(f"  429 on {dst.name}; waiting {wait}s")
                time.sleep(wait)
                continue
            raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "data" / "downloads"))
    args = ap.parse_args()
    out = Path(args.out)
    for local, name in {**PHOTOS, **PATTAYA, **AMATEUR}.items():
        try:
            fetch(commons_url(name), out / local)
        except Exception as e:  # a truncated title in the list: say so, keep going
            print(f"  ! {local}: {e}")
    for local, (name, key) in {**VIDEOS, **AMATEUR_VIDEOS}.items():
        try:
            fetch(commons_url(name, key), out / local)
        except Exception as e:
            print(f"  ! {local}: {e}")
    print(f"done -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
