#!/usr/bin/env python3
"""
refresh.py — Rebuild data/seed.json from the Louisiana Secretary of State's
published "NewCorp" weekly newsletters (new business filings).

Source: https://corafiles.sos.la.gov/NewCorpNewsletters/NewCorp_YYYYMMDD.txt
These are plain-text files the LA Secretary of State publishes weekly listing
new domestic charters and new foreign qualifications: charter number, filing
date, entity name, mailing address, registered agent(s), officers/members.

Politeness: descriptive User-Agent, >=1.5s between requests, small pulls only
(default: the 2 most recent weekly files). No crawling of the CORAWeb search
interface — only the published newsletter files are fetched.

Usage:
    python3 refresh.py [--weeks 2] [--out data/seed.json]
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

BASE = "https://corafiles.sos.la.gov/NewCorpNewsletters/NewCorp_{}.txt"
UA = "LA-New-Business-Filings-API/0.1 (seed refresh; small-sample; contact: local developer)"
DELAY = 1.5  # seconds between requests

# ---------------------------------------------------------------------------
# City -> Louisiana parish mapping (parish = Louisiana county equivalent).
# Only the principal parish is listed for cities that span more than one.
# Cities not in this table get parish=None (reported honestly in coverage).
# ---------------------------------------------------------------------------
CITY_PARISH = {
    # New Orleans / metro
    "NEW ORLEANS": "Orleans", "METAIRIE": "Jefferson", "KENNER": "Jefferson",
    "HARVEY": "Jefferson", "GRETNA": "Jefferson", "MARRERO": "Jefferson",
    "WESTWEGO": "Jefferson", "JEFFERSON": "Jefferson", "HARAHAN": "Jefferson",
    "RIVER RIDGE": "Jefferson", "TERRYTOWN": "Jefferson", "AVONDALE": "Jefferson",
    "WAGGAMAN": "Jefferson", "CHALMETTE": "St. Bernard", "ARABI": "St. Bernard",
    "MERAUX": "St. Bernard", "VIOLET": "St. Bernard", "ST. BERNARD": "St. Bernard",
    "POYDRAS": "St. Bernard", "BELLE CHASSE": "Plaquemines", "BURAS": "Plaquemines",
    "PORT SULPHUR": "Plaquemines", "EMPIRE": "Plaquemines",
    "LULING": "St. Charles", "DESTREHAN": "St. Charles", "HAHNVILLE": "St. Charles",
    "NORCO": "St. Charles", "ST. ROSE": "St. Charles", "BOUTTE": "St. Charles",
    "PARADIS": "St. Charles", "DES ALLEMANDS": "St. Charles", "AMA": "St. Charles",
    "MONTZ": "St. Charles", "LAPLACE": "St. John the Baptist",
    "RESERVE": "St. John the Baptist", "GARYVILLE": "St. John the Baptist",
    "EDGARD": "St. John the Baptist", "WALLACE": "St. John the Baptist",
    "CONVENT": "St. James", "GRAMERCY": "St. James", "LUTCHER": "St. James",
    "VACHERIE": "St. James", "PAULINA": "St. James",
    # Baton Rouge / capital region
    "BATON ROUGE": "East Baton Rouge", "BAKER": "East Baton Rouge",
    "ZACHARY": "East Baton Rouge", "CENTRAL": "East Baton Rouge",
    "GREENWELL SPRINGS": "East Baton Rouge", "PRIDE": "East Baton Rouge",
    "DENHAM SPRINGS": "Livingston", "WALKER": "Livingston",
    "LIVINGSTON": "Livingston", "ALBANY": "Livingston", "SPRINGFIELD": "Livingston",
    "FRENCH SETTLEMENT": "Livingston", "HOLDEN": "Livingston", "WATSON": "Livingston",
    "PORT VINCENT": "Livingston", "MAUREPAS": "Livingston",
    "GONZALES": "Ascension", "PRAIRIEVILLE": "Ascension", "GEISMAR": "Ascension",
    "DONALDSONVILLE": "Ascension", "ST. AMANT": "Ascension", "SORRENTO": "Ascension",
    "GALVEZ": "Ascension", "PORT ALLEN": "West Baton Rouge",
    "BRUSLY": "West Baton Rouge", "ADDIS": "West Baton Rouge",
    "ERWINVILLE": "West Baton Rouge", "PLAQUEMINE": "Iberville",
    "WHITE CASTLE": "Iberville", "MARINGOUIN": "Iberville",
    "ST. GABRIEL": "Iberville", "CARVILLE": "Iberville", "ROSEDALE": "Iberville",
    "GROSSE TETE": "Iberville", "NEW ROADS": "Pointe Coupee",
    "LIVONIA": "Pointe Coupee", "MORGANZA": "Pointe Coupee",
    "LAKELAND": "Pointe Coupee", "VENTRESS": "Pointe Coupee",
    "JARREAU": "Pointe Coupee", "ST. FRANCISVILLE": "West Feliciana",
    "JACKSON": "East Feliciana", "CLINTON": "East Feliciana",
    "ETHEL": "East Feliciana", "SLAUGHTER": "East Feliciana",
    "WILSON": "East Feliciana", "NORWOOD": "East Feliciana",
    "GREENSBURG": "St. Helena", "MONTPELIER": "St. Helena",
    "PINE GROVE": "St. Helena",
    # Northshore
    "SLIDELL": "St. Tammany", "COVINGTON": "St. Tammany",
    "MANDEVILLE": "St. Tammany", "PEARL RIVER": "St. Tammany",
    "LACOMBE": "St. Tammany", "ABITA SPRINGS": "St. Tammany",
    "MADISONVILLE": "St. Tammany", "FOLSOM": "St. Tammany",
    "BUSH": "St. Tammany", "SUN": "St. Tammany",
    "HAMMOND": "Tangipahoa", "PONCHATOULA": "Tangipahoa", "AMITE": "Tangipahoa",
    "KENTWOOD": "Tangipahoa", "INDEPENDENCE": "Tangipahoa",
    "ROSELAND": "Tangipahoa", "TICKFAW": "Tangipahoa", "LORANGER": "Tangipahoa",
    "NATALBANY": "Tangipahoa", "ROBERT": "Tangipahoa",
    "BOGALUSA": "Washington", "FRANKLINTON": "Washington", "ANGIE": "Washington",
    "VARNADO": "Washington", "MT. HERMON": "Washington", "PINE": "Washington",
    # Acadiana
    "LAFAYETTE": "Lafayette", "YOUNGSVILLE": "Lafayette", "BROUSSARD": "Lafayette",
    "SCOTT": "Lafayette", "CARENCRO": "Lafayette", "DUSON": "Lafayette",
    "MILTON": "Lafayette", "MAURICE": "Vermilion", "ABBEVILLE": "Vermilion",
    "KAPLAN": "Vermilion", "ERATH": "Vermilion", "GUEYDAN": "Vermilion",
    "DELCAMBRE": "Iberia", "NEW IBERIA": "Iberia", "JEANERETTE": "Iberia",
    "LOREAUVILLE": "Iberia", "BREAUX BRIDGE": "St. Martin",
    "ST. MARTINVILLE": "St. Martin", "CECILIA": "St. Martin",
    "PARKS": "St. Martin", "HENDERSON": "St. Martin",
    "OPELOUSAS": "St. Landry", "PORT BARRE": "St. Landry",
    "KROTZ SPRINGS": "St. Landry", "GRAND COTEAU": "St. Landry",
    "SUNSET": "St. Landry", "ARNAUDVILLE": "St. Landry",
    "LEONVILLE": "St. Landry", "MELVILLE": "St. Landry",
    "WASHINGTON": "St. Landry", "EUNICE": "St. Landry",
    "CROWLEY": "Acadia", "RAYNE": "Acadia", "CHURCH POINT": "Acadia",
    "IOTA": "Acadia", "ESTHERWOOD": "Acadia", "MORSE": "Acadia",
    "MIDLAND": "Acadia", "BRANCH": "Acadia", "MAXIE": "Acadia",
    "VILLE PLATTE": "Evangeline", "MAMOU": "Evangeline",
    "TURKEY CREEK": "Evangeline", "PINE PRAIRIE": "Evangeline",
    "BASILE": "Evangeline", "CHATAIGNIER": "Evangeline", "REDDELL": "Evangeline",
    "JENNINGS": "Jefferson Davis", "WELSH": "Jefferson Davis",
    "LAKE ARTHUR": "Jefferson Davis", "ROANOKE": "Jefferson Davis",
    "ELTON": "Jefferson Davis", "FENTON": "Jefferson Davis",
    "LACASSINE": "Jefferson Davis", "FRANKLIN": "St. Mary",
    "MORGAN CITY": "St. Mary", "PATTERSON": "St. Mary", "BERWICK": "St. Mary",
    "BALDWIN": "St. Mary", "CENTERVILLE": "St. Mary",
    # Bayou region
    "HOUMA": "Terrebonne", "BOURG": "Terrebonne", "CHAUVIN": "Terrebonne",
    "MONTEGUT": "Terrebonne", "DULAC": "Terrebonne", "GRAY": "Terrebonne",
    "SCHRIEVER": "Terrebonne", "THIBODAUX": "Lafourche", "RACELAND": "Lafourche",
    "LOCKPORT": "Lafourche", "LAROSE": "Lafourche", "CUT OFF": "Lafourche",
    "GALLIANO": "Lafourche", "GOLDEN MEADOW": "Lafourche", "CHACKBAY": "Lafourche",
    "NAPOLEONVILLE": "Assumption", "PIERRE PART": "Assumption",
    "LABADIEVILLE": "Assumption", "BELLE ROSE": "Assumption",
    # Southwest
    "LAKE CHARLES": "Calcasieu", "SULPHUR": "Calcasieu", "WESTLAKE": "Calcasieu",
    "MOSS BLUFF": "Calcasieu", "IOWA": "Calcasieu", "VINTON": "Calcasieu",
    "DEQUINCY": "Calcasieu", "STARKS": "Calcasieu", "BELL CITY": "Calcasieu",
    "HAYES": "Calcasieu", "KINDER": "Allen", "OBERLIN": "Allen",
    "ELIZABETH": "Allen", "REEVES": "Allen", "OAKDALE": "Allen",
    "DERIDDER": "Beauregard", "RAGLEY": "Beauregard", "SINGER": "Beauregard",
    "MERRYVILLE": "Beauregard", "LONGVILLE": "Beauregard",
    "LEESVILLE": "Vernon", "ANACOCO": "Vernon", "HORNBECK": "Vernon",
    "SIMPSON": "Vernon", "ROSEPINE": "Vernon", "NEW LLANO": "Vernon",
    "PITKIN": "Vernon", "EVANS": "Vernon",
    # Central
    "ALEXANDRIA": "Rapides", "PINEVILLE": "Rapides", "BALL": "Rapides",
    "BOYCE": "Rapides", "LECOMPTE": "Rapides", "WOODWORTH": "Rapides",
    "DEVILLE": "Rapides", "TIOGA": "Rapides", "GLENMORA": "Rapides",
    "FOREST HILL": "Rapides", "BUNKIE": "Avoyelles", "MARKSVILLE": "Avoyelles",
    "COTTONPORT": "Avoyelles", "MANSURA": "Avoyelles", "SIMMESPORT": "Avoyelles",
    "MOREAUVILLE": "Avoyelles", "HESSMER": "Avoyelles",
    "BORDELONVILLE": "Avoyelles", "PLAUCHEVILLE": "Avoyelles",
    "NATCHITOCHES": "Natchitoches", "CAMPTI": "Natchitoches",
    "PROVENCAL": "Natchitoches", "ROBELINE": "Natchitoches",
    "MARTHAVILLE": "Natchitoches", "CLARENCE": "Natchitoches",
    "GOLDONNA": "Natchitoches", "ASHLAND": "Natchitoches",
    "COLFAX": "Grant", "MONTGOMERY": "Grant", "DRY PRONG": "Grant",
    "POLLOCK": "Grant", "BENTLEY": "Grant", "CREOLA": "Grant",
    "GEORGETOWN": "Grant", "WINNFIELD": "Winn", "DODSON": "Winn",
    "CALVIN": "Winn", "ATLANTA": "Winn", "Sikes": "Winn", "SIKES": "Winn",
    "JENA": "LaSalle", "OLLA": "LaSalle", "TULLOS": "LaSalle", "URANIA": "LaSalle",
    # Northwest
    "SHREVEPORT": "Caddo", "BELCHER": "Caddo", "BLANCHARD": "Caddo",
    "MOORINGSPORT": "Caddo", "IDA": "Caddo", "RODESSA": "Caddo",
    "HOSSTON": "Caddo", "GILLIAM": "Caddo", "DIXIE": "Caddo",
    "KEITHVILLE": "Caddo", "GREENWOOD": "Caddo", "OIL CITY": "Caddo",
    "VIVIAN": "Caddo", "BOSSIER CITY": "Bossier", "HAUGHTON": "Bossier",
    "BENTON": "Bossier", "PLAIN DEALING": "Bossier",
    "MINDEN": "Webster", "SIBLEY": "Webster", "DUBBERLY": "Webster",
    "HEFLIN": "Webster", "DOYLINE": "Webster", "DIXIE INN": "Webster",
    "SAREPTA": "Webster", "COTTON VALLEY": "Webster", "SHONGALOO": "Webster",
    "SPRINGHILL": "Webster", "CULLEN": "Webster",
    "STONEWALL": "De Soto", "KEATCHIE": "De Soto", "GLOSTER": "De Soto",
    "FRIERSON": "De Soto", "GRAND CANE": "De Soto", "MANSFIELD": "De Soto",
    "LOGANSPORT": "De Soto", "LONGSTREET": "De Soto", "PELICAN": "De Soto",
    "MANY": "Sabine", "ZWOLLE": "Sabine", "CONVERSE": "Sabine",
    "NOBLE": "Sabine", "EBARB": "Sabine", "BELMONT": "Sabine",
    "FLORIEN": "Sabine", "NEGREET": "Sabine", "PLEASANT HILL": "Sabine",
    "COUSHATTA": "Red River", "HALL SUMMIT": "Red River", "MARTIN": "Red River",
    "HOMER": "Claiborne", "HAYNESVILLE": "Claiborne", "ATHENS": "Claiborne",
    "LISBON": "Claiborne", "SUMMERFIELD": "Claiborne",
    "RUSTON": "Lincoln", "DUBACH": "Lincoln", "CHOUDRANT": "Lincoln",
    "SIMSBORO": "Lincoln", "GRAMBLING": "Lincoln", "VIENNA": "Lincoln",
    "ARCADIA": "Bienville", "GIBSLAND": "Bienville", "BIENVILLE": "Bienville",
    "CASTOR": "Bienville", "RINGGOLD": "Bienville", "JAMESTOWN": "Bienville",
    "LUCKY": "Bienville", "SALINE": "Bienville", "MOUNT LEBANON": "Bienville",
    "BRYCELAND": "Bienville", "TAYLOR": "Bienville",
    "JONESBORO": "Jackson", "QUITMAN": "Jackson", "HODGE": "Jackson",
    # Northeast
    "MONROE": "Ouachita", "WEST MONROE": "Ouachita", "STERLINGTON": "Ouachita",
    "CALHOUN": "Ouachita", "SWARTZ": "Ouachita", "RICHWOOD": "Ouachita",
    "FARMERVILLE": "Union", "BERNICE": "Union", "MARION": "Union",
    "SPEARSVILLE": "Union", "LILLIE": "Union", "DOWNSVILLE": "Union",
    "BASTROP": "Morehouse", "MER ROUGE": "Morehouse", "COLLINSTON": "Morehouse",
    "BONITA": "Morehouse", "OAK RIDGE": "Morehouse",
    "RAYVILLE": "Richland", "START": "Richland", "MANGHAM": "Richland",
    "DELHI": "Richland", "TALLULAH": "Madison", "RICHMOND": "Madison",
    "DELTA": "Madison", "WINNSBORO": "Franklin", "BASKIN": "Franklin",
    "GILBERT": "Franklin", "WISNER": "Franklin", "CROWVILLE": "Franklin",
    "LAKE PROVIDENCE": "East Carroll", "TRANSYLVANIA": "East Carroll",
    "OAK GROVE": "West Carroll", "KILBOURNE": "West Carroll",
    "FOREST": "West Carroll", "PIONEER": "West Carroll", "EPPS": "West Carroll",
    "ST. JOSEPH": "Tensas", "NEWELLTON": "Tensas", "WATERPROOF": "Tensas",
    "VIDALIA": "Concordia", "CLAYTON": "Concordia", "RIDGECREST": "Concordia",
    "FERRIDAY": "Concordia", "MONTEREY": "Concordia", "WILDSVILLE": "Concordia",
    "JONESVILLE": "Catahoula", "SICILY ISLAND": "Catahoula",
    "HARRISONBURG": "Catahoula", "MANIFEST": "Catahoula",
    "COLUMBIA": "Caldwell", "GRAYSON": "Caldwell", "KELLY": "Caldwell",
    "CLARKS": "Caldwell",
}

# Charter-number suffix -> entity type (Louisiana SoS convention, heuristic).
# The newsletter does not print an explicit type field; the suffix on the
# charter number encodes the filing type. Documented as heuristic in SOURCES.md.
SUFFIX_TYPE = {
    "K": "LLC",
    "D": "Corporation",
    "N": "Nonprofit Corporation",
    "E": "Foreign Entity",
    "EE": "Foreign Entity",
}

MAIN_RE = re.compile(r"^\s+(\d{6,10}[A-Z]{0,2})\s+(\d{1,2}/\d{1,2}/\d{4})\s+(.*\S)\s*$")
ROLE_RE = re.compile(r"^\s+(Agent|Officer|Member/Manager|Manager|Member):\s+(.*\S)\s*$")
# Tail of a US address: "..., ST 70586" or "..., ST 70586-5708" or "..., ST 705865708"
ADDR_TAIL_RE = re.compile(
    r"^(?P<rest>.*),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-?(?P<zip4>\d{4}))?$"
)
# Fallback for non-US tails, e.g. "8500 DECARIE BLVD, #400 MONT-ROYAL, QC, H4P2N2"
ADDR_TAIL_FOREIGN_RE = re.compile(
    r"^(?P<street>.*),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2}),\s*(?P<zip>\S+)$"
)
# Known Louisiana cities, longest first, for splitting street from city.
# Aliases: sources write both "ST. AMANT" and "SAINT AMANT", "MT." / "MOUNT".
_ALIASES = {}
for _k in CITY_PARISH:
    _ALIASES[_k] = _k
    if _k.startswith("ST. "):
        _ALIASES["SAINT " + _k[4:]] = _k
    if _k.startswith("MT. "):
        _ALIASES["MOUNT " + _k[4:]] = _k
CITY_KEYS = sorted(_ALIASES.keys(), key=len, reverse=True)


def split_street_city(rest):
    """Split '123 MAIN ST BATON ROUGE' -> ('123 MAIN ST', 'BATON ROUGE')."""
    up = rest.upper()
    for key in CITY_KEYS:
        if up.endswith(" " + key):
            return rest[: -(len(key) + 1)].strip(), _ALIASES[key].title()
    words = rest.split()
    if len(words) >= 3 and not words[-2].isdigit():
        return " ".join(words[:-2]), " ".join(words[-2:]).title()
    if len(words) >= 2:
        return " ".join(words[:-1]), words[-1].title()
    return rest, None


def pretty_name(raw):
    """Title-case an ALL-CAPS entity name, restoring common suffix styling."""
    name = raw.title() if raw.isupper() else raw
    name = re.sub(r"\bLlc\b", "LLC", name)
    name = re.sub(r"\bLlp\b", "LLP", name)
    name = re.sub(r"\bInc\b(?!\.)", "Inc.", name)
    name = re.sub(r"\bCorp\b(?!\.)", "Corp.", name)
    return name


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def find_recent_files(weeks):
    """Walk back from today to find the most recent existing newsletter files."""
    found = []
    day = date.today()
    for _ in range(21):  # search up to 3 weeks back for the newest file
        url = BASE.format(day.strftime("%Y%m%d"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    found.append((day, url))
                    if len(found) >= weeks:
                        break
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
        day -= timedelta(days=1)
        time.sleep(0.4)
    return found


def split_name_address(rest):
    parts = re.split(r"\s{2,}", rest.strip())
    if len(parts) == 1:
        return parts[0].strip(), ""
    return parts[0].strip(), " ".join(p.strip() for p in parts[1:])


def parse_newsletter(text, source_url, source_file):
    records = []
    origin = "domestic"
    current = None

    def flush():
        if current:
            records.append(current.copy())

    for line in text.splitlines():
        if "Foreign Charters:" in line:
            flush(); current = None; origin = "foreign"; continue
        if "Domestic Charters:" in line:
            flush(); current = None; origin = "domestic"; continue
        m = MAIN_RE.match(line)
        if m:
            flush()
            charter, fdate, rest = m.groups()
            name, address = split_name_address(rest)
            city = state = zipc = None
            am = ADDR_TAIL_RE.match(address) if address else None
            if am:
                _, city = split_street_city(am.group("rest"))
                state = am.group("state")
                zipc = am.group("zip")
            elif address:
                fm = ADDR_TAIL_FOREIGN_RE.match(address)
                if fm:
                    city = fm.group("city").strip().title()
                    state = fm.group("state")
                    zipc = fm.group("zip")
            suffix = re.search(r"([A-Z]+)$", charter)
            suffix = suffix.group(1) if suffix else ""
            etype = SUFFIX_TYPE.get(suffix, "Other")
            if origin == "foreign" and etype == "Other":
                etype = "Foreign Entity"
            month, day_, year = fdate.split("/")
            current = {
                "id": charter,
                "charter_number": charter,
                "name": pretty_name(name),
                "name_raw": name,
                "entity_type": etype,
                "origin": origin,
                "filing_date": f"{year}-{int(month):02d}-{int(day_):02d}",
                "address": address,
                "city": city,
                "state": state,
                "zip": zipc,
                "parish": CITY_PARISH.get(city.upper()) if city and state == "LA" else None,
                "registered_agent": None,
                "agents": [],
                "source_file": source_file,
                "source_url": source_url,
            }
            continue
        r = ROLE_RE.match(line)
        if r and current is not None:
            role, value = r.groups()
            person = re.split(r"\s{2,}", value.strip())[0].strip()
            if role == "Agent" and person:
                pname = person.title() if person.isupper() else person
                if pname not in current["agents"]:
                    current["agents"].append(pname)
    flush()
    for rec in records:
        rec["registered_agent"] = rec["agents"][0] if rec["agents"] else None
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=2, help="weekly files to include")
    ap.add_argument("--no-merge", action="store_true",
                    help="replace the dataset instead of merging into the existing one")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "seed.json"))
    args = ap.parse_args()

    files = find_recent_files(args.weeks)
    if not files:
        print("ERROR: no newsletter files found in the last 3 weeks", file=sys.stderr)
        return 1
    print(f"Found {len(files)} newsletter file(s):")
    by_charter = {}
    for day, url in files:
        print(f"  fetching {url}")
        text = fetch(url)
        recs = parse_newsletter(text, url, f"NewCorp_{day.strftime('%Y%m%d')}.txt")
        print(f"    parsed {len(recs)} records")
        for rec in recs:
            by_charter.setdefault(rec["charter_number"], rec)  # newest first wins
        time.sleep(DELAY)

    out = Path(args.out)
    sources = [url for _, url in files]
    # Merge into the existing dataset (e.g. the backfilled history) instead
    # of replacing it: freshly parsed records win, older history is kept.
    if not args.no_merge and out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            kept = 0
            for rec in old.get("records", []):
                if rec["charter_number"] not in by_charter:
                    by_charter[rec["charter_number"]] = rec
                    kept += 1
            for url in old.get("sources", []):
                if url not in sources:
                    sources.append(url)
            print(f"  merged with existing dataset: kept {kept} older records")
        except (ValueError, KeyError, OSError) as exc:
            print(f"  WARNING: could not merge existing dataset ({exc}); writing fresh")

    records = sorted(by_charter.values(), key=lambda r: (r["filing_date"], r["name_raw"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "Louisiana New Business Filings (Secretary of State NewCorp newsletters)",
        "generated_at": date.today().isoformat(),
        "record_count": len(records),
        "sources": sources,
        "records": records,
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    with_parish = sum(1 for r in records if r["parish"])
    print(f"Wrote {len(records)} records to {out} ({with_parish} with parish assigned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
