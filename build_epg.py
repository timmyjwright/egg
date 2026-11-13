#!/usr/bin/env python3
"""Build a merged, self-updating XMLTV EPG for TiviMate.

Downloads 7 gzipped XMLTV sources from epgshare01.online, keeps everything
from the first 6, filters the US locals file down to 6 cities, merges them
into one valid XMLTV document and writes custom_epg.xml.gz.

Standard library only (no pip dependencies).
"""

import gzip
import io
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

BASE_URL = "https://epgshare01.online/epgshare01/"

# Files kept in full (all <channel> and <programme> tags).
FULL_SOURCES = [
    "epg_ripper_UK1.xml.gz",
    "epg_ripper_US2.xml.gz",
    "epg_ripper_US_SPORTS1.xml.gz",
    "epg_ripper_PEACOCK1.xml.gz",
    "epg_ripper_AU1.xml.gz",
    "epg_ripper_BEIN1.xml.gz",
]

# File filtered to the listed US cities only.
LOCALS_SOURCE = "epg_ripper_US_LOCALS1.xml.gz"

LOCAL_CITY_TERMS = [
    "los angeles",
    "san francisco",
    "bay area",
    "sacramento",
    "new york",
    "miami",
    "reno",
]

LOCAL_CALLSIGNS = [
    "KCBS", "KNBC", "KABC", "KTTV", "KTLA",            # Los Angeles
    "KPIX", "KNTV", "KGO", "KTVU",                    # San Francisco / Bay Area
    "KOVR", "KCRA", "KXTV", "KTXL",                   # Sacramento
    "WCBS", "WNBC", "WABC", "WNYW", "WPIX",           # New York
    "WFOR", "WTVJ", "WPLG", "WSVN",                   # Miami
    "KTVN", "KRNV", "KOLO", "KRXI",                   # Reno
]

# Case-insensitive, word-boundary match so e.g. "KGO" does not match "KGOOD".
LOCAL_PATTERN = re.compile(
    r"\b(" + "|".join(
        re.escape(t) for t in LOCAL_CITY_TERMS + LOCAL_CALLSIGNS
    ) + r")\b",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

OUTPUT_FILE = "custom_epg.xml.gz"


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


def channel_matches_local(channel_elem):
    """True if a <channel> matches a wanted city or call sign."""
    haystacks = [channel_elem.get("id", "")]
    haystacks.extend(
        dn.text or "" for dn in channel_elem.findall("display-name")
    )
    return any(LOCAL_PATTERN.search(h) for h in haystacks if h)


def process_source(data, keep_local_filter, out_file, kept_channel_ids):
    """Stream one gzipped XMLTV source, writing kept elements to out_file.

    When keep_local_filter is False every channel/programme is kept.
    When True, a first pass collects matching channel ids and a second pass
    writes those channels and their programmes.
    """
    wanted_ids = None
    if keep_local_filter:
        wanted_ids = set()
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
                    if channel_matches_local(elem):
                        wanted_ids.add(elem.get("id"))
                elem.clear()
                root.clear()  # drop root's child refs so RAM stays flat
        print(f"  local filter kept {len(wanted_ids)} channels")

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
                keep = (wanted_ids is None or cid in wanted_ids)
                if keep and cid not in kept_channel_ids:
                    kept_channel_ids.add(cid)
                    out_file.write(ET.tostring(elem, encoding="utf-8"))
                    out_file.write(b"\n")
                    stats["channels"] += 1
                elif keep:
                    stats["dupe_channels"] += 1
            elif elem.tag == "programme":
                if wanted_ids is None or elem.get("channel") in wanted_ids:
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


def main():
    sources_ok = 0
    totals = {"channels": 0, "programmes": 0, "dupe_channels": 0}
    kept_channel_ids = set()

    with gzip.open(OUTPUT_FILE, "wb") as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        out.write(b'<tv generator-info-name="build_epg.py">\n')

        for filename in FULL_SOURCES:
            data = download(BASE_URL + filename)
            if data is None:
                continue
            sources_ok += 1
            stats = process_source(data, False, out, kept_channel_ids)
            for key in totals:
                totals[key] += stats[key]

        data = download(BASE_URL + LOCALS_SOURCE)
        if data is not None:
            sources_ok += 1
            stats = process_source(data, True, out, kept_channel_ids)
            for key in totals:
                totals[key] += stats[key]

        out.write(b"</tv>\n")

    print(
        f"Done: {sources_ok}/7 sources used, "
        f"{totals['channels']} channels, "
        f"{totals['programmes']} programmes "
        f"({totals['dupe_channels']} duplicate channel ids skipped) "
        f"-> {OUTPUT_FILE}"
    )

    if sources_ok == 0:
        print("ERROR: every source failed; no guide built.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
