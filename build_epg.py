#!/usr/bin/env python3
"""Build a trimmed, self-updating XMLTV EPG for TiviMate.

Downloads gzipped XMLTV sources from epgshare01.online, keeps only the
channels that are actually watched (see the filter lists below), merges
them into two valid XMLTV documents: custom_epg_uk.xml.gz and
custom_epg_us.xml.gz (add each as its own EPG source in TiviMate).

The previous version kept 2,068 channels / ~300,000 programmes (~14 MB
gzipped), which TiviMate on the Fire TV Cube would not import fully.
This version keeps roughly 1,100 channels and under half the programmes.

Standard library only (no pip dependencies).
"""

import gzip
import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BASE_URL = "https://epgshare01.online/epgshare01/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Channel filters
#
# Channels are matched case-insensitively on their id, with any run of
# non-letters/digits treated as a dot (so "SkySp F1 HD" matches "skysp.f1").
# To get a channel back, delete or edit the line that drops it.
# ---------------------------------------------------------------------------

# US national/cable (epg_ripper_US2): DROP these groups, keep the rest.
# Kept: US news, entertainment, movies, kids, sport (incl. ESPN channels,
# NFL Network/RedZone, regional sports), Paramount+ with Showtime.
US2_DROP = {
    "adult": r"playboy|hustler|brazzers|penthouse|vividtv|bang\.u|adult\.on\.demand|reality\.kings",
    "shopping": r"\bqvc|\bhsn|shophq|shop\.lc|shopnow|jewelry\.television|directv\.valu|celebrity\.shopping|oferta\.tv|compre\.aqui|infast|anheuser|prime\.revenue",
    "religious": r"3abn|ewtn|daystar|trinity\.broadcasting|tbn\.inspire|god\.tv|sonlife|victory\.channel|living\.faith|catholic\.faith|nrb\.tv|word\.network|christian\.television|eternal\.word|hope\.channel|danbi|enlace|sembrador|golden\.eagle|impact\.network|positiv|pure\.flix|faith\.and\.living|jewish\.life|byu-tv|\btct\b",
    "music video": r"\bvevo|mtv-u|mtv\.classic|\bmtv2\b|mtvlive|bet\.jams|bet\.soul|bet\.gospel|nick\.music|\bfm\.hd|\brevolt",
    "regional news": r"spectrum\.news|news\.12\.|necn\.|nbc\.chicago\.\(live",
    "out-of-market packages": r"nba\.league\.pass|mls\.direct\.kick|mlb\.extra\.innings|nfl\.sunday\.ticket",
    # Non-English / overseas channels.
    "international": r"univision|telemundo|unimas|galavision|estrella|\btudn|deportes|cine\.mexicano|cine\.estelar|cine\.latino|cinema\.dinamita|cine\.nostalgia|de\.pelicula|pasiones|\bwapa|mega\.tv|mexicanal|nuestra|telefe|caracol|ecuavisa|sur\.peru|peru\.magico|tv\.chile|tc\.internacional|centroam|antena\.3|\batres|\btve\b|rai\.|mediaset|zee\.tv|aapka|gma\.|\btfc\b|filipino|kapatid|sari\.sari|pinoy|\bmyx|\bkbs|\bnhk|\bsbs|\bytn|zhong|cctv|china\.global|phoenix|charming\.china|viet|saigon|s-channel|\bjbs\b|dzmm|ebs\.america|mbc\.america|opm\.tv|telecentro|rtv\.internacional|multimedios|video\.rola|bandamax|hogar|hola!|\bvme\b|ve\.plus|\bonce\b|hitn|zona\.futbol|gol\.tv|tyc\.sports|real\.madrid|\batn\b|\bn\+",
}

# UK (epg_ripper_UK1): DROP these groups, keep the rest.
# Kept: BBC, ITV/ITVX-era channels, Channel 4/5, Sky (entertainment, cinema,
# sports incl. F1 and Premier League, documentaries, kids), TNT Sports,
# UK news, Premier Sports, box office/PPV.
UK1_DROP = {
    "radio": r"bbc\.r|\bradio\b|\.fm\b|talksport|kerrang|absolute\.|\bheart|capital|smooth\.uk|jazz\.fm|planet\.rock|classic\.rock|greatest\.hits",
    "shopping": r"qvc|tjc|gems\.tv|jewellery|hobbymaker|create\.and\.craft|ideal\.extra|best\.direct|tv\.warehouse|high\.street|cruise1st|craft\.store",
    "religious": r"god\.channel|ewtn|islam\.tv|akaal|madani|noor\.tv|iqra|loveworld|faith|sukh\.sagar|takbeer|ahlebait|kicc|revelation",
    "adult": r"babes|brazzers|adult\.channel|\bxxx|studio\.66|playboy|hustler",
    # Non-English / overseas channels (geo.(news|tv) is GEO, not Nat Geo).
    "international": r"utsav|sony\.|zee\.|colors|hum\.|geo\.(news|tv)|ptv\.|aastha|brit\.asia|b4u|ndtv|arirang|kanshi|matv|vox\.africa|iran\.intl|trt\.world|news\.arabia|ntd\.uk|rté|virgin\.media",
}

# US sports packages (epg_ripper_US_SPORTS1): keep NFL, NBA, MLB, NHL, MLS,
# WNBA, UFL and UFC team/event channels; DROP minor league baseball.
# (Matched after normalisation, so the hyphen is a dot.)
US_SPORTS1_DROP = r"^milb\."

# How much guide to keep. epgshare01 carries about 5 months ahead, which is
# most of the file size. TiviMate only needs the next couple of days.
LOOKBACK_HOURS = 12   # keep programmes that started recently (still airing)
HOURS_AHEAD = 48      # keep programmes starting within the next 2 days

# US locals (epg_ripper_US_LOCALS1): KEEP ONLY these - ABC, NBC, CBS, FOX
# and CW affiliates for San Francisco, Los Angeles, Miami and Reno.
LOCALS_KEEP = {
    # San Francisco
    "KGO-DT.us_locals1",   # ABC
    "KPIX-DT.us_locals1",  # CBS
    "KNTV-DT.us_locals1",  # NBC
    "KTVU-DT.us_locals1",  # FOX
    "KPYX-DT.us_locals1",  # CW
    # Los Angeles
    "KABC-DT.us_locals1",  # ABC
    "KCBS-DT.us_locals1",  # CBS
    "KNBC-DT.us_locals1",  # NBC
    "KTTV-DT.us_locals1",  # FOX
    "KTLA-DT.us_locals1",  # CW
    # Miami
    "WPLG-DT.us_locals1",  # ABC
    "WFOR-DT.us_locals1",  # CBS
    "WTVJ-DT.us_locals1",  # NBC
    "WSVN-DT.us_locals1",  # FOX
    "WSFL-DT.us_locals1",  # CW
    # Reno
    "KOLO-DT.us_locals1",  # ABC
    "KTVN-DT.us_locals1",  # CBS
    "KRNV-DT.us_locals1",  # NBC
    "KRXI-DT.us_locals1",  # FOX
    "KNSN-DT.us_locals1",  # CW
}

# Sources to merge, in order. The Australia (AU1) and beIN MENA (BEIN1)
# files from the original spec are no longer fetched: they were not in the
# requested channel list. To re-add one, append its filename here with
# None as the drop rules, e.g. ("epg_ripper_BEIN1.xml.gz", None).
GUIDES = {
    "custom_epg_uk.xml.gz": [
        ("epg_ripper_UK1.xml.gz", UK1_DROP, None),
    ],
    "custom_epg_us.xml.gz": [
        ("epg_ripper_US2.xml.gz", US2_DROP, None),
        ("epg_ripper_US_SPORTS1.xml.gz", {"minor league baseball": US_SPORTS1_DROP}, None),
        ("epg_ripper_PEACOCK1.xml.gz", None, None),
        ("epg_ripper_US_LOCALS1.xml.gz", None, LOCALS_KEEP),
    ],
}


def normalise(text):
    """Lower-case and fold any run of non-alphanumerics to a single dot."""
    return re.sub(r"[^a-z0-9]+", ".", text.lower()).strip(".")


def download(url):
    """Return the response body for url, or None after logging a warning."""
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 - any failure means "skip this source"
        print(f"WARNING: could not download {url}: {exc}", file=sys.stderr)
        return None


def channel_kept(channel_elem, drop_rules, keep_set):
    """Decide whether a <channel> survives the filters."""
    cid = channel_elem.get("id", "")
    if keep_set is not None:
        return cid in keep_set
    if drop_rules:
        norm_id = normalise(cid)
        for pattern in drop_rules.values():
            if re.search(pattern, norm_id):
                return False
    return True


def parse_xmltv_time(value):
    """Parse an XMLTV timestamp such as '20261113200000 +0000' (UTC)."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip()[:14], "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def programme_in_window(prog_elem, not_before, not_after):
    """True if a <programme> overlaps the kept time window."""
    start = parse_xmltv_time(prog_elem.get("start"))
    stop = parse_xmltv_time(prog_elem.get("stop")) or start
    if start is None:
        return True  # no usable time; keep it rather than risk a gap
    return stop >= not_before and start <= not_after


def process_source(data, drop_rules, keep_set, out_file, kept_channel_ids):
    """Stream one gzipped XMLTV source, writing kept elements to out_file.

    First pass collects the ids of the channels that survive the filters;
    second pass writes those channels and their programmes.
    """
    wanted_ids = set()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        root = None
        for event, elem in ET.iterparse(gz, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag == "channel" and channel_kept(elem, drop_rules, keep_set):
                wanted_ids.add(elem.get("id"))
            if elem.tag in ("channel", "programme"):
                elem.clear()
                root.clear()  # drop root's child refs so RAM stays flat
    print(f"  filter kept {len(wanted_ids)} channels")

    now = datetime.now(timezone.utc)
    not_before = now - timedelta(hours=LOOKBACK_HOURS)
    not_after = now + timedelta(hours=HOURS_AHEAD)

    stats = {"channels": 0, "programmes": 0, "dupe_channels": 0}
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        root = None
        for event, elem in ET.iterparse(gz, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag not in ("channel", "programme"):
                continue
            if elem.tag == "channel":
                cid = elem.get("id")
                if cid in wanted_ids and cid not in kept_channel_ids:
                    kept_channel_ids.add(cid)
                    out_file.write(ET.tostring(elem, encoding="utf-8"))
                    out_file.write(b"\n")
                    stats["channels"] += 1
                elif cid in wanted_ids:
                    stats["dupe_channels"] += 1
            elif elem.tag == "programme":
                if elem.get("channel") in wanted_ids and programme_in_window(
                    elem, not_before, not_after
                ):
                    out_file.write(ET.tostring(elem, encoding="utf-8"))
                    out_file.write(b"\n")
                    stats["programmes"] += 1
            elem.clear()
            root.clear()  # drop root's child refs so RAM stays flat
    print(
        f"  kept {stats['channels']} channels, "
        f"{stats['programmes']} programmes, "
        f"skipped {stats['dupe_channels']} duplicate channel ids"
    )
    return stats


def build_guide(output_file, sources, cache):
    """Build one guide file from its sources. Returns (ok_count, totals)."""
    sources_ok = 0
    totals = {"channels": 0, "programmes": 0, "dupe_channels": 0}
    kept_channel_ids = set()

    with gzip.open(output_file, "wb") as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        out.write(b'<tv generator-info-name="build_epg.py">\n')

        for filename, drop_rules, keep_set in sources:
            if filename not in cache:
                cache[filename] = download(BASE_URL + filename)
            data = cache[filename]
            if data is None:
                continue
            sources_ok += 1
            stats = process_source(data, drop_rules, keep_set, out, kept_channel_ids)
            for key in totals:
                totals[key] += stats[key]

        out.write(b"</tv>\n")

    print(
        f"Done {output_file}: {sources_ok}/{len(sources)} sources used, "
        f"{totals['channels']} channels, {totals['programmes']} programmes"
    )
    return sources_ok, totals


def main():
    cache = {}
    failed = False
    for output_file, sources in GUIDES.items():
        ok, _ = build_guide(output_file, sources, cache)
        if ok == 0:
            print(f"ERROR: every source failed for {output_file}.", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
