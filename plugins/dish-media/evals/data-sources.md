# Eval data sources

Real files the evals run on, fetched by `fetch_data.py` into the git-ignored
`results/data/downloads/`. All from Wikimedia Commons; none is redistributed here.

| Local name | Commons file | Licence |
|---|---|---|
| `hk/N13P_01..11.jpg` | `HK 上環 Sheung Wan … SC Cuisine Chinese Seafood Restaurant food … siu yuk October 2025 N13P 01–11.jpg` | CC0 |
| `mixed/khmer_01..04.jpg` | `Khmer Family Restaurant, 2018 (01–04).jpg` | CC BY-SA 4.0 |
| `mixed/turkey_starter.jpg` | `Starter dish in Turkey.jpg` | CC BY-SA 4.0 |
| `mixed/lazarus_grilled_fish.jpg` | `Grilled fish, Lazaru's Restaurant, New Urbane Hotel, 2025 (01).jpg` | CC BY-SA 4.0 |
| `mixed/meatloaf.jpg` | `Dish of meatloaf served on a white plate with sauce and herbs in a restaurant setting.jpg` | CC BY 2.0 |
| `video/moon_turntable.webm` | `Moon Essentials- Turntable (SVS5319).webm` (NASA SVS, 1080p transcode) | Public domain |
| `video/swift_360.webm` | `Swift Spacecraft Animations- 2025 (SVS14786 - SWIFT 360 4k 60fps, cropped).webm` (NASA SVS, 1080p transcode) | Public domain |
| `video/al92_rotation.webm` | `Exposed Central Rotation of AL92 (CIRA 2024-06-21 - nolabels).webm` (1080p transcode) | Public domain |
| `pattaya/*.jpg` (15) | `DFC 1093 …`, `DFC 5021 …`, `DFC 1025 …`, `DFC 1238 …`, `DFC 1493 …`, `DFC 5103 …`, `DFC 2288 …`, `DFC 5113 …`, `DFC 1021 …`, `DFC 4533 …`, `DFC 3983 …`, `DZ6 1891 …`, `DSCF0811 …`, `DSCF0812 …`, `DFC 0313 …` (single plated dishes by PattayaPatrol; full titles in `fetch_data.py`) | CC BY-SA 4.0 |

| `amateur/*.jpg` (24) | Unedited phone snapshots of restaurant meals by several Commons uploaders (Nando's, Guzman y Gomez, Red Rooster, KFC, NeNe Chicken, Albion and Inglewood hotels, Top Fryz, Kedai Juragan, Griffins Hotel, Pizza Hut Berlin, Pod Siódemką, Oasis Voi, Cafe de Coral, Shenzhen and Zhongshan dinners, Tokyo Italian, Giorgina Wien, Lisbon goat, Mérida dinner, ILiat schnitzel, Kway Teow); full titles in `fetch_data.py` | CC BY-SA 4.0 / CC BY 2.0 / CC0 |
| `video-amateur/*.webm` (7) | Handheld restaurant phone videos on Commons (Silk Road London ×2, Mujo wagyu, Shima teppanyaki, fast food Seoul, plov, brochettes Lyon), 1080p/720p transcodes | CC BY-SA / CC BY |

Real rotating-food clips (not on Commons), fetched by hand into `results/data/downloads/video-real/`:

| Local name | Source | Licence |
|---|---|---|
| `mixkit_rotating_bowl_fruit.mp4` | Mixkit "Rotating bowl with fruit on a white background" (10424), 720p | Mixkit Stock Video Free License |
| `mixkit_rotating_chocolate_cake.mp4` | Mixkit "Slowly rotating chocolate cake seen from above" (41124), 720p portrait | Mixkit Stock Video Free License |
| `pexels_raspberries_black_plate.mp4` | Pexels video 37710291 | Pexels License |
| `pexels_tomato_juice_plate.mp4` | Pexels video 37710296 | Pexels License |
| `pexels_baked_dish.mp4` | Pexels video 6162079 | Pexels License |
| `pexels_spinning_cake_stand.mp4` | Pexels video 8478028 | Pexels License |
| `pexels_rotating_cake_stand.mp4` | Pexels video 7525335 (a person in frame; the content trap) | Pexels License |
| `pexels_gourmet_dumplings.mp4`, `pexels_girl_rotating_cake_stand.mp4`, `pexels_manti_dumplings.mp4` | Pexels 37626631, 8899618, 37296046 (fetched, not used) | Pexels License |

Fetch the Mixkit files from `https://assets.mixkit.co/videos/<id>/<id>-720.mp4` and the Pexels files from
`https://www.pexels.com/download/video/<id>/?w=1920&h=1080` with a browser user agent.

The HK set is one phone, one restaurant, eight minutes: the closest thing on
Commons to a real session (one dish from eleven angles). The mixed set is
several photographers and lights on purpose. The Pattaya set is fifteen
different plated dishes by one photographer. No food turntable clip exists on
Commons, so `loops-and-hdr` uses three turntable-style renders and `loops-real`
the Mixkit and Pexels clips above; `build_eval_inputs.py` re-encodes them as
phone-like H.264 `.mov` files and tags one of each set as HLG.
