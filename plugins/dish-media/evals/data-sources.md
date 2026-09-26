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

The HK set is one phone, one restaurant, eight minutes: the closest thing on
Commons to a real session (one dish from eleven angles). The mixed set is
several photographers and lights on purpose. No food turntable clip exists on
Commons, so the loops use three turntable-style renders; `build_eval_inputs.py`
re-encodes them as phone-like H.264 `.mov` files and tags one as HLG.
