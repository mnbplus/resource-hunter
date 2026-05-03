from __future__ import annotations

from dataclasses import dataclass

from .common import detect_platform
from .intent import parse_intent
from .models import SearchResult
from .ranking import deduplicate_results, diversify_results, score_result


@dataclass
class SearchBenchmarkCase:
    query: str
    kind: str
    expected_title: str
    candidates: list[SearchResult]
    adversarial: bool = False


@dataclass
class VideoBenchmarkCase:
    url: str
    expected_platform: str


def _torrent(title: str, source: str, hash_id: str, seeders: int, upstream: str = "") -> SearchResult:
    return SearchResult(
        channel="torrent",
        normalized_channel="torrent",
        source=source,
        upstream_source=upstream or source,
        provider="magnet",
        title=title,
        link_or_magnet=f"magnet:?xt=urn:btih:{hash_id}",
        share_id_or_info_hash=hash_id,
        seeders=seeders,
    )


def _pan(title: str, provider: str, share_id: str, source: str = "ps.252035", password: str = "") -> SearchResult:
    return SearchResult(
        channel="pan",
        normalized_channel="pan",
        source=source,
        upstream_source=source,
        provider=provider,
        title=title,
        link_or_magnet=f"https://example.com/share/{share_id}",
        share_id_or_info_hash=share_id,
        password=password,
    )


def _movie_cases(name: str, year: str, alt: str, bad: str, offset: int) -> list[SearchBenchmarkCase]:
    base = f"{offset}"
    strong = f"{name} {year} 2160p BluRay REMUX HDR"
    bluray = f"{name} {year} 1080p BluRay"
    return [
        SearchBenchmarkCase(
            query=f"{name} {year}",
            kind="movie",
            expected_title=strong,
            candidates=[
                _torrent(strong, "tpb", f"m{base}1", 60),
                _torrent(bluray, "yts", f"m{base}2", 80),
                _torrent(f"{alt} {year} 2160p BluRay REMUX HDR", "tpb", f"m{base}3", 120),
                _torrent(f"{bad} {year} 1080p HDCAM", "tpb", f"m{base}4", 20, upstream="plugin:thepiratebay"),
            ],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{name} {year} 4K",
            kind="movie",
            expected_title=strong,
            candidates=[
                _torrent(strong, "tpb", f"m{base}5", 32),
                _torrent(bluray, "yts", f"m{base}6", 70),
                _torrent(f"{alt} {year} 2160p WEB-DL", "tpb", f"m{base}7", 90),
            ],
        ),
        SearchBenchmarkCase(
            query=f"{name} {year} BluRay",
            kind="movie",
            expected_title=bluray,
            candidates=[
                _torrent(bluray, "tpb", f"m{base}8", 44),
                _torrent(f"{name} {year} 1080p DTS-HD MA 5.1 REMUX", "tpb", f"m{base}9", 10),
                _torrent(f"{bad} {year} 1080p HDCAM", "tpb", f"m{base}10", 88, upstream="plugin:thepiratebay"),
            ],
            adversarial=True,
        ),
    ]


def _episode_cases(kind: str, name: str, season: int, episode: int, bad: str, offset: int) -> list[SearchBenchmarkCase]:
    base = f"{offset}"
    se = f"S{season:02d}E{episode:02d}"
    exact = f"{name} {se} 1080p WEB-DL"
    pilot = f"{name} {se} Pilot 1080p WEB-DL"
    good_source = "nyaa" if kind == "anime" else "eztv"
    return [
        SearchBenchmarkCase(
            query=f"{name} {se}",
            kind=kind,
            expected_title=exact,
            candidates=[
                _torrent(exact, good_source, f"e{base}1", 40),
                _torrent(f"{name} {se} 2160p WEBRip", "tpb", f"e{base}2", 8),
                _torrent(f"{bad} {se} 1080p WEB", "tpb", f"e{base}3", 150),
            ],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{name} Season {season} Episode {episode}",
            kind=kind,
            expected_title=exact,
            candidates=[
                _torrent(exact, good_source, f"e{base}4", 42),
                _torrent(f"{bad} {se} 1080p WEB", "tpb", f"e{base}5", 140),
                _torrent(f"{name} S{season:02d}E{episode + 1:02d} 1080p WEB-DL", "tpb", f"e{base}6", 55),
            ],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{name} {se} pilot",
            kind=kind,
            expected_title=pilot,
            candidates=[
                _torrent(pilot, "tpb", f"e{base}7", 20, upstream="plugin:thepiratebay"),
                _torrent(f"{bad} {se} 2160p WEBRip", "tpb", f"e{base}8", 120),
                _torrent(f"{name} S{season:02d}E{episode + 2:02d} 1080p WEB-DL", good_source, f"e{base}9", 45),
            ],
            adversarial=True,
        ),
    ]


def _music_cases(artist: str, work: str, bad: str, offset: int) -> list[SearchBenchmarkCase]:
    base = f"{offset}"
    exact = f"{artist} - {work} FLAC"
    return [
        SearchBenchmarkCase(
            query=f"{artist} {work} \u65e0\u635f",
            kind="music",
            expected_title=exact,
            candidates=[_pan(exact, "aliyun", f"mu{base}1", password="1234"), _pan(f"{artist} - {work} MP3", "aliyun", f"mu{base}2"), _pan(f"{bad} FLAC", "aliyun", f"mu{base}3")],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{artist} {work} flac",
            kind="music",
            expected_title=exact,
            candidates=[_pan(exact, "quark", f"mu{base}4", source="pansou"), _pan(f"{artist} - {work} MP3", "aliyun", f"mu{base}5")],
        ),
        SearchBenchmarkCase(
            query=f"{artist} soundtrack",
            kind="music",
            expected_title=exact,
            candidates=[_pan(exact, "aliyun", f"mu{base}6"), _pan(f"{bad} MP3", "aliyun", f"mu{base}7")],
        ),
    ]


def _software_cases(name: str, version: str, bad: str, offset: int) -> list[SearchBenchmarkCase]:
    base = f"{offset}"
    exact = f"{name} {version} Windows x64"
    return [
        SearchBenchmarkCase(
            query=f"{name} {version}",
            kind="software",
            expected_title=exact,
            candidates=[_pan(exact, "quark", f"sw{base}1"), _pan(f"{bad} {version} Windows x64", "quark", f"sw{base}2")],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{name} {version} windows",
            kind="software",
            expected_title=exact,
            candidates=[_pan(exact, "aliyun", f"sw{base}3"), _pan(f"{name} {version} macOS", "aliyun", f"sw{base}4")],
        ),
        SearchBenchmarkCase(
            query=f"{name} installer",
            kind="software",
            expected_title=exact,
            candidates=[_pan(exact, "baidu", f"sw{base}5", source="pansou"), _pan(f"{bad} {version} Windows x64", "aliyun", f"sw{base}6")],
            adversarial=True,
        ),
    ]


def _book_cases(name: str, alt: str, fmt: str, offset: int) -> list[SearchBenchmarkCase]:
    base = f"{offset}"
    exact = f"{name} {fmt}"
    return [
        SearchBenchmarkCase(
            query=f"{name} {fmt}",
            kind="book",
            expected_title=exact,
            candidates=[_pan(exact, "aliyun", f"bk{base}1"), _pan(f"{alt} {fmt}", "aliyun", f"bk{base}2")],
            adversarial=True,
        ),
        SearchBenchmarkCase(
            query=f"{name} ebook",
            kind="book",
            expected_title=exact,
            candidates=[_pan(exact, "quark", f"bk{base}3", source="pansou"), _pan(f"{alt} pdf", "aliyun", f"bk{base}4")],
        ),
        SearchBenchmarkCase(
            query=f"{name} pdf",
            kind="book",
            expected_title=f"{name} pdf",
            candidates=[_pan(f"{name} pdf", "aliyun", f"bk{base}5"), _pan(f"{alt} pdf", "aliyun", f"bk{base}6")],
        ),
    ]


def _search_cases() -> list[SearchBenchmarkCase]:
    cases: list[SearchBenchmarkCase] = []
    _movie_seeds: list[tuple[str, str, str, str]] = [
        ("Oppenheimer", "2023", "Interstellar", "Open Hearts"),
        ("Dune Part Two", "2024", "Arrival", "Dune Drift"),
        ("The Matrix", "1999", "The Thirteenth Floor", "Matrixed"),
        ("Blade Runner", "1982", "Tron", "Blade Nation"),
        ("Spirited Away", "2001", "Howls Moving Castle", "Spirits Bay"),
        ("Inception", "2010", "Tenet", "Insurrection"),
        ("Parasite", "2019", "Memories of Murder", "Parallel Site"),
        ("Mad Max Fury Road", "2015", "Furiosa", "Mad World"),
        ("The Dark Knight", "2008", "Batman Begins", "The Night Watch"),
        ("Whiplash", "2014", "La La Land", "Wind Clash"),
    ]
    for index, (name, year, alt, bad) in enumerate(_movie_seeds, start=1):
        cases.extend(_movie_cases(name, year, alt, bad, offset=index))

    _tv_seeds: list[tuple[str, int, int, str]] = [
        ("Breaking Bad", 1, 1, "The Bad Guys Breaking In"),
        ("Severance", 2, 1, "Server Room"),
        ("The Bear", 2, 10, "Bearly Legal"),
        ("Dark", 1, 1, "Dark Matter"),
        ("Shogun", 1, 2, "Shotgun Wedding"),
        ("Andor", 1, 3, "Android Files"),
        ("Succession", 1, 1, "Successful People"),
        ("The Last of Us", 1, 5, "The Last Bus"),
        ("Silo", 1, 2, "Solo"),
        ("Mr Robot", 1, 1, "My Robot"),
    ]
    for index, (name, season, episode, bad) in enumerate(_tv_seeds, start=1):
        cases.extend(_episode_cases("tv", name, season, episode, bad, offset=index))

    _anime_seeds: list[tuple[str, int, int, str]] = [
        ("Attack on Titan", 1, 1, "Titan Attackers"),
        ("Frieren", 1, 1, "Frozen End"),
        ("Demon Slayer", 1, 1, "Slayer Demo"),
        ("One Piece", 1, 1, "Single Piece"),
        ("Naruto", 1, 1, "Narrow Route"),
        ("Jujutsu Kaisen", 1, 1, "Jungle Kaisen"),
        ("Bleach", 1, 1, "Beach"),
        ("Mob Psycho 100", 1, 1, "Mob City"),
        ("Steins Gate", 1, 1, "Stone Gate"),
        ("Cowboy Bebop", 1, 1, "Cowboy Rehab"),
    ]
    for index, (name, season, episode, bad) in enumerate(_anime_seeds, start=1):
        cases.extend(_episode_cases("anime", name, season, episode, bad, offset=index))

    _music_seeds: list[tuple[str, str, str]] = [
        ("Jay Chou", "Fantasy", "Joy Ride"),
        ("Taylor Swift", "Red", "Red Sun"),
        ("Hans Zimmer", "Interstellar OST", "Interior Lines"),
        ("Aimer", "Brave Shine", "Brave Signal"),
        ("Eason Chan", "U87", "U89"),
        ("Yiruma", "River Flows In You", "River Falls"),
        ("Joe Hisaishi", "Spirited Away OST", "Spiral Drift"),
        ("Daft Punk", "Random Access Memories", "Random Memory"),
        ("Kenshi Yonezu", "Lemon", "Melon"),
        ("Hikaru Utada", "First Love", "Frost Love"),
    ]
    for index, (artist, work, bad) in enumerate(_music_seeds, start=1):
        cases.extend(_music_cases(artist, work, bad, offset=index))

    _software_seeds: list[tuple[str, str, str]] = [
        ("Adobe Photoshop", "2024", "Adobe Illustrator"),
        ("Visual Studio Code", "1.90", "Visual Studio"),
        ("PyCharm Professional", "2024", "IntelliJ IDEA"),
        ("AutoCAD", "2025", "SketchUp"),
        ("Microsoft Office", "2021", "LibreOffice"),
        ("Final Cut Pro", "10.8", "Premiere Pro"),
        ("Ableton Live", "12", "FL Studio"),
        ("OBS Studio", "30", "Streamlabs"),
        ("Affinity Photo", "2.5", "Affinity Designer"),
        ("Blender", "4.1", "Maya"),
    ]
    for index, (name, version, bad) in enumerate(_software_seeds, start=1):
        cases.extend(_software_cases(name, version, bad, offset=index))

    _book_seeds: list[tuple[str, str, str]] = [
        ("Three Body Problem", "Three Kingdoms", "epub"),
        ("The Pragmatic Programmer", "The Mythical Man-Month", "pdf"),
        ("Clean Code", "Code Complete", "pdf"),
        ("Deep Learning", "Pattern Recognition", "pdf"),
        ("The Hobbit", "The Lord of the Rings", "epub"),
        ("Harry Potter", "Percy Jackson", "epub"),
        ("Dune", "Foundation", "epub"),
        ("The Art of Computer Programming", "Introduction to Algorithms", "pdf"),
        ("Sapiens", "Homo Deus", "epub"),
        ("The Name of the Wind", "The Wise Mans Fear", "pdf"),
    ]
    for index, (name, alt, fmt) in enumerate(_book_seeds, start=1):
        cases.extend(_book_cases(name, alt, fmt, offset=index))
    return cases


def _video_cases() -> list[VideoBenchmarkCase]:
    samples = [
        ("https://www.youtube.com/watch?v=demo1", "YouTube"),
        ("https://youtu.be/demo2", "YouTube"),
        ("https://www.bilibili.com/video/BV123", "Bilibili"),
        ("https://b23.tv/demo", "Bilibili"),
        ("https://www.tiktok.com/@user/video/1", "TikTok"),
        ("https://www.douyin.com/video/1", "Douyin"),
        ("https://www.instagram.com/reel/1", "Instagram"),
        ("https://x.com/user/status/1", "Twitter/X"),
        ("https://weibo.com/1", "Weibo"),
        ("https://vimeo.com/1", "Vimeo"),
    ]
    return [VideoBenchmarkCase(url, platform) for url, platform in samples for _ in range(2)]


def _evaluate_search_case(case: SearchBenchmarkCase) -> dict[str, int | bool]:
    intent = parse_intent(
        case.query,
        explicit_kind=case.kind,
        wants_4k="4k" in case.query.lower(),
        wants_sub="subtitle" in case.query.lower() or "\u5b57\u5e55" in case.query,
    )
    scored = [score_result(candidate, intent, cache=None) for candidate in case.candidates]
    ordered = diversify_results(deduplicate_results(scored))
    top1 = bool(ordered and ordered[0].title == case.expected_title)
    top3 = any(item.title == case.expected_title for item in ordered[:3])
    high_conf = [item for item in ordered[:3] if item.tier == "top"]
    return {
        "top1": top1,
        "top3": top3,
        "high_conf_count": len(high_conf),
        "high_conf_errors": len([item for item in high_conf if item.title != case.expected_title]),
        "adversarial_failure": case.adversarial and any(item.title != case.expected_title and item.tier == "top" for item in ordered[:3]),
    }


def run_benchmark_suite() -> dict[str, object]:
    search_cases = _search_cases()
    video_cases = _video_cases()
    by_kind: dict[str, dict[str, int]] = {}
    overall_top1 = overall_top3 = overall_high_conf = overall_high_conf_errors = 0
    adversarial_failures = 0
    for case in search_cases:
        result = _evaluate_search_case(case)
        bucket = by_kind.setdefault(case.kind, {"cases": 0, "top1": 0, "top3": 0})
        bucket["cases"] += 1
        bucket["top1"] += 1 if result["top1"] else 0
        bucket["top3"] += 1 if result["top3"] else 0
        overall_top1 += 1 if result["top1"] else 0
        overall_top3 += 1 if result["top3"] else 0
        overall_high_conf += int(result["high_conf_count"])
        overall_high_conf_errors += int(result["high_conf_errors"])
        adversarial_failures += 1 if result["adversarial_failure"] else 0

    video_passes = 0
    for vcase in video_cases:
        if parse_intent(vcase.url).kind == "video" and detect_platform(vcase.url) == vcase.expected_platform:
            video_passes += 1

    by_kind_rates = {
        kind: {
            "cases": stats["cases"],
            "top1_rate": stats["top1"] / stats["cases"] if stats["cases"] else 0.0,
            "top3_rate": stats["top3"] / stats["cases"] if stats["cases"] else 0.0,
        }
        for kind, stats in by_kind.items()
    }
    overall = {
        "top1_rate": overall_top1 / len(search_cases) if search_cases else 0.0,
        "top3_rate": overall_top3 / len(search_cases) if search_cases else 0.0,
        "high_conf_error_rate": overall_high_conf_errors / overall_high_conf if overall_high_conf else 0.0,
    }
    thresholds = {
        "overall_top1": overall["top1_rate"] >= 0.90,
        "overall_top3": overall["top3_rate"] >= 0.97,
        "kind_top1": all(stats["top1_rate"] >= 0.85 for stats in by_kind_rates.values()),
        "kind_top3": all(stats["top3_rate"] >= 0.92 for stats in by_kind_rates.values()),
        "high_conf_error_rate": overall["high_conf_error_rate"] <= 0.02,
        "adversarial_top": adversarial_failures == 0,
        "video_cases": video_passes == len(video_cases),
    }
    return {
        "schema_version": "3",
        "search_cases": len(search_cases),
        "video_cases": len(video_cases),
        "overall": overall,
        "by_kind": by_kind_rates,
        "adversarial": {"cases": len([case for case in search_cases if case.adversarial]), "top_failures": adversarial_failures},
        "thresholds": thresholds,
        "pass": all(thresholds.values()),
    }


__all__ = ["run_benchmark_suite"]
