from quarry.intent import build_plan, parse_intent


def test_detects_kinds_and_fields():
    tv = parse_intent("Breaking Bad S01E01")
    assert tv.kind == "tv"
    assert tv.season == 1
    assert tv.episode == 1

    music = parse_intent("周杰伦 无损")
    assert music.kind == "music"

    anime = parse_intent("进击的巨人 Attack on Titan")
    assert anime.kind == "anime"
    assert anime.english_alias == "Attack on Titan"

    movie = parse_intent("Oppenheimer 2023")
    assert movie.kind == "movie"
    assert movie.year == "2023"

    video = parse_intent("https://youtu.be/test")
    assert video.kind == "video"
    assert video.is_video_url is True


def test_build_plan_respects_routing_preferences():
    anime = parse_intent("进击的巨人 Attack on Titan", wants_sub=True)
    anime_plan = build_plan(anime)
    assert anime_plan.channels == ["torrent", "pan"]
    assert anime_plan.preferred_torrent_sources[0] == "torznab"
    assert anime_plan.preferred_torrent_sources == ["torznab", "nyaa", "dmhy", "bangumi_moe", "bitsearch", "torrentgalaxy", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "eztv", "yts", "knaben", "solidtorrents", "btdig"]

    movie = parse_intent("Oppenheimer 2023", wants_4k=True)
    movie_plan = build_plan(movie)
    assert movie_plan.channels == ["pan", "torrent"]
    assert movie_plan.preferred_torrent_sources[0] == "torznab"
    assert movie_plan.preferred_torrent_sources[1] == "yts"
    assert any("2160p" in item.lower() or "4k" in item.lower() for item in movie_plan.pan_queries + movie_plan.torrent_queries)

    # Test software intent priorities
    software = parse_intent("Adobe Photoshop cc")
    software_plan = build_plan(software)
    assert software_plan.preferred_pan_sources == ["upyunso", "pansou", "ps.252035", "panhunt"]
    assert software_plan.preferred_torrent_sources == ["torznab", "torrentmac", "fitgirl", "torrentgalaxy", "bitsearch", "tpb", "1337x", "limetorrents", "torlock", "ext_to", "nyaa", "knaben", "solidtorrents", "btdig"]
