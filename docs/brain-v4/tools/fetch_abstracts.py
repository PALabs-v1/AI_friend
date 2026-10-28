"""Fetch source abstracts for brain-v4 citations, so claims are checked against
the source text rather than a search-engine summary.

Usage (from brain-v4/): python3 tools/fetch_abstracts.py > research/abstracts.md
DOIs resolve through the Europe PMC REST API, falling back to Crossref;\narXiv IDs through the arXiv API.
"""

import json
import re
import sys
import urllib.parse
import urllib.request

DOIS = {
    "stivers2009": "10.1073/pnas.0903616106",
    "levinson2016": "10.1016/j.tics.2015.10.010",
    "roberts_delvecchio2000": "10.1037/0033-2909.126.1.3",
    "roberts2006": "10.1037/0033-2909.132.1.1",
    "killingsworth2010": "10.1126/science.1192439",
    "gray2007": "10.1126/science.1134475",
    "strachan2024": "10.1038/s41562-024-01882-z",
    "barrett2019": "10.1177/1529100619832930",
    "barrett_simmons2015": "10.1038/nrn3950",
    "dorkenwald2024": "10.1038/s41586-024-07558-y",
    "shiu2024": "10.1038/s41586-024-07763-9",
    "kirkpatrick2017": "10.1073/pnas.1611835114",
    "man_damasio2019": "10.1038/s42256-019-0103-7",
    "defreitas_jcr": "10.1093/jcr/ucaf040",
    # added 2026-09-26 from Codex R4-R6, adopted into 01-what-is-possible.md
    "jones_bergen2026_pnas": "10.1073/pnas.2524472123",
    "skjuve2023": "10.1093/iwc/iwad022",
    "keramati_gutkin2014": "10.7554/eLife.04811",
    "reece2023_candor": "10.1126/sciadv.adf3197",
    "peng2025_fdbench": "10.21437/Interspeech.2025-739",
    "lavan2025": "10.1371/journal.pone.0332692",
    "ehret2021": "10.1145/3486580",
    "ho_macdorman2019": "10.1007/s12369-019-00516-z",
    "vandergoot2024": "10.1007/s00146-023-01818-7",
    "greenwood_garfinkel2025": "10.1146/annurev-psych-020924-125202",
    "pospisil2024": "10.1038/s41586-024-07982-0",
}

ARXIV = {
    "jones_bergen2025": "2503.23674",
    "butlin2023": "2308.08708",
    "park2023": "2304.03442",
    "park2024": "2411.10109",
    "defossez2024_moshi": "2410.00037",
    "chalmers2023": "2303.07103",
    "defreitas2024_arxiv": "2407.19096",
    "guingrich_graziano2025": "2509.19515",
    "laurencon2021_hrrl": "2109.06580",
    "lin2025_fullduplexbench": "2503.04721",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "brain-v4-citation-check/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def europepmc(doi: str) -> dict:
    q = urllib.parse.quote(f'DOI:"{doi}"')
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={q}&resultType=core&format=json"
    )
    results = json.loads(get(url))["resultList"]["result"]
    if not results:
        return {}
    r = results[0]
    journal = r.get("journalInfo", {})
    return {
        "title": r.get("title"),
        "authors": r.get("authorString"),
        "venue": (journal.get("journal") or {}).get("title"),
        "year": r.get("pubYear"),
        "volume": journal.get("volume"),
        "pages": r.get("pageInfo"),
        "abstract": re.sub(r"<[^>]+>", "", r.get("abstractText") or ""),
    }


def crossref(doi: str) -> dict:
    m = json.loads(get(f"https://api.crossref.org/works/{doi}"))["message"]
    authors = ", ".join(
        f"{a.get('family', '')} {a.get('given', '')}".strip()
        for a in m.get("author", [])
    )
    return {
        "title": (m.get("title") or [""])[0],
        "authors": authors,
        "venue": (m.get("container-title") or [""])[0],
        "year": str((m.get("issued", {}).get("date-parts") or [[""]])[0][0]),
        "volume": m.get("volume"),
        "pages": m.get("page"),
        "abstract": re.sub(r"<[^>]+>", "", m.get("abstract", "")),
        "source": "crossref",
    }


def arxiv(aid: str) -> dict:
    xml = get(f"https://export.arxiv.org/api/query?id_list={aid}").decode()
    entry = xml.split("<entry>", 1)[-1]

    def tag(name: str) -> str:
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", entry, re.DOTALL)
        return " ".join(m.group(1).split()) if m else ""

    authors = re.findall(r"<name>(.*?)</name>", entry)
    return {
        "title": tag("title"),
        "authors": ", ".join(authors),
        "venue": "arXiv",
        "year": tag("published")[:4],
        "abstract": tag("summary"),
    }


def emit(out, key: str, link: str, rec: dict) -> None:
    out.write(f"## {key}\n\n{link}\n\n")
    if not rec:
        out.write("NOT FOUND\n\n")
        return
    for field, value in rec.items():
        if value:
            out.write(f"- **{field}**: {value}\n")
    out.write("\n")


def main() -> None:
    out = sys.stdout
    out.write("# Source abstracts (fetched by tools/fetch_abstracts.py)\n\n")
    for key, doi in DOIS.items():
        try:
            rec = europepmc(doi) or crossref(doi)
        except Exception as exc:  # a network failure is recorded, not hidden
            rec = {"error": repr(exc)}
        emit(out, key, f"DOI: https://doi.org/{doi}", rec)
    for key, aid in ARXIV.items():
        try:
            rec = arxiv(aid)
        except Exception as exc:
            rec = {"error": repr(exc)}
        emit(out, key, f"arXiv: https://arxiv.org/abs/{aid}", rec)


if __name__ == "__main__":
    main()
