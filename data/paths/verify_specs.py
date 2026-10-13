#!/usr/bin/env python3
"""
Verification script for Globalise JSONPath entity specifications.

Checks performed:
1. Syntax validation of all YAML specification files (both flat and section-based).
2. Explicit specification of target JSON file patterns (e.g. `polity/*.json`, `dynasty/*.json`).
3. Strict assertion that `_label` is never present in any path or filter expression.
4. Syntax parsing of all JSONPath expressions using `jsonpath_ng.ext.parse`.
5. Live evaluation of JSONPath expressions against real JSON-LD objects from
   the Surf object store catalog (with support for local cached fallbacks).
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from jsonpath_ng.ext import parse as parse_jsonpath
except ImportError:
    sys.exit(
        "Error: 'jsonpath_ng' is required. Run via 'uv run python verify_specs.py' "
        "or install it with 'pip install jsonpath-ng'."
    )

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
except ImportError:
    requests = None

BASE_OBJECTSTORE_URL = "https://objectstore.surf.nl/87435b768620494e8e911c83d1997f24:globalise-data/objects/"

# Test target samples mapped by entity section or filename
SECTION_SAMPLE_TARGETS = {
    "person": [
        ("Titus van Bengalen", "person/Person_12189.json"),
        ("Petronella Prinsloo", "person/Person_18492.json"),
        ("Adam Ried", "person/Person_256826.json"),
        ("Johannes Haak", "person/Person_32387.json"),
        ("Gerrit Chardijn", "person/Person_391479.json"),
        ("Jan de Meijer", "person/Person_467674.json"),
        ("John Cooling", "person/Person_540151.json"),
        ("Memdert Tollesz", "person/Person_607304.json"),
        ("Dirk Jansz: Bragenstroom", "person/Person_674680.json"),
        ("Telemachus van de Caab", "person/Person_8512.json"),
    ],
    "place": [
        ("Toli-Toli", "place/GLOB2_1162.json"),
        ("Fort Dansborg", "place/GLOB2_1322.json"),
        ("Chikuzen", "place/GLOB2_E_246.json"),
        ("Sarangani Island", "place/GLOB_452.json"),
        ("Iwaki", "place/GLOB2_E_85.json"),
        ("Caduatte-corle", "place/GLOB_733.json"),
        ("Davao Region", "place/NEW_ADMIN_1039.json"),
        ("Vijayanarayanam", "place/GLOB_NB_170.json"),
        ("St: Jago (Church)", "place/NR_250_HUM_1.json"),
        ("Edamuttam", "place/NR_186.json"),
    ],
    "organization": [
        ("Central administration in Asia", "group/ORG_98.json"),
        ("ORG_27", "group/ORG_27.json"),
        ("Ambon (Comptoir)", "group/ORG_112.json"),
        ("Cambelle", "group/ORG_166.json"),
        ("Banda Weeskamer", "group/ORG_196.json"),
        ("Batavia College van Diakenen", "group/ORG_162.json"),
        ("Administration in the Netherlands", "group/ORG_89.json"),
        ("Colombo", "group/ORG_67.json"),
        ("Palembang (Comptoir)", "group/ORG_121.json"),
        ("ORG_99", "group/ORG_99.json"),
    ],
    "polity": [
        ("Polity Raijua", "polity/Polity_263.json"),
        ("Amabi", "polity/Polity_152.json"),
        ("Polity Portugal", "polity/Polity_55.json"),
        ("Buleleng", "polity/Polity_99.json"),
        ("Fatuaruin (Liurai Wehali)", "polity/Polity_193.json"),
        ("Funai", "polity/Polity_196.json"),
        ("Donggala (Banawa)", "polity/Polity_190.json"),
        ("Madura (West)", "polity/Polity_142.json"),
        ("Negeri Sembilan", "polity/Polity_255.json"),
        ("Bijapur", "polity/Polity_47.json"),
    ],
    "dynasty": [
        ("Rulership of Bilah Lineage 1", "dynasty/Dynasty_165.json"),
        ("Rulership of Kubu Lineage 1", "dynasty/Dynasty_244.json"),
        ("Champassak Dynasty", "dynasty/Dynasty_107.json"),
        ("Rulership of Amanuban Lineage 1", "dynasty/Dynasty_129.json"),
        ("Lineage 4 Dynasty", "dynasty/Dynasty_457.json"),
        ("Aravidu Dynasty", "dynasty/Dynasty_23.json"),
        ("Rulership of Lombok Lineage 5(Pagutan...", "dynasty/Dynasty_445.json"),
        ("Banten Dynasty", "dynasty/Dynasty_40.json"),
        ("Rulership of Fatuaruin (Liurai Wehali...", "dynasty/Dynasty_197.json"),
        ("Rulership of Dynasty Zand", "dynasty/Dynasty_71.json"),
    ],
    "reign": [
        ("The Reign of Senerat", "reign/Reign_0182.json"),
        ("The Reign of Tunibatta", "reign/Reign_0403.json"),
        ("The Reign of Mohammed Thaher", "reign/Reign_0644.json"),
        ("The Reign of Musa Malikuddin", "reign/Reign_1559.json"),
        ("The Reign of La Balawo", "reign/Reign_1119.json"),
        ("The Reign of Andi Makassaung ri Langi", "reign/Reign_2026.json"),
        ("The Reign of Ki Gede Kapulungan", "reign/Reign_2414.json"),
        ("The Reign of Gusti Agung Ngurah Made ...", "reign/Reign_2288.json"),
        ("The Reign of Marcus Jacob Dalero", "reign/Reign_2677.json"),
        ("The Reign of Alung Yunus", "reign/Reign_2888.json"),
    ],
    "ship": [
        ("Ship Durgerdam", "ship/DAS_ship0351.json"),
        ("Ship Oostrust", "ship/DAS_ship1107.json"),
        ("Ship Goa", "ship/gzm_ship1309.json"),
        ("Ship De Hoop", "ship/gzm_ship2712.json"),
        ("Ship [Hendrik Zandijk's pancalang]", "ship/monsoon_traders_ship1070.json"),
        ("Ship [paduwakang 1581]", "ship/monsoon_traders_ship1581.json"),
        ("Ship [paduwakang 2525]", "ship/monsoon_traders_ship2525.json"),
        ("Ship [paduwakang 4469]", "ship/monsoon_traders_ship4469.json"),
        ("Ship [paduwakang 5331]", "ship/monsoon_traders_ship5331.json"),
        ("Ship [paduwakang 5574]", "ship/monsoon_traders_ship5574.json"),
    ],
    "voyage": [
        ("Voyage 10054", "voyage/10054.json"),
        ("Voyage 13203", "voyage/13203.json"),
        ("Voyage 17106", "voyage/17106.json"),
        ("Voyage 21222", "voyage/21222.json"),
        ("Voyage 24005", "voyage/24005.json"),
        ("Voyage Identified by 770915IA", "voyage/770915IA.json"),
        ("Voyage 92314", "voyage/92314.json"),
        ("Voyage 96286", "voyage/96286.json"),
        ("Voyage Identified by gzm_voyage0708", "voyage/gzm_voyage0708.json"),
        ("Voyage Identified by gzm_voyage4992", "voyage/gzm_voyage4992.json"),
    ],
    "occurrence": [
        ("Occurrence of Currency Westvriesche D...", "occurrences/CO_0053.json"),
        ("Occurrence of Currency Keizerdaalder ...", "occurrences/CO_0045.json"),
        ("Occurrence of Currency Dubbelde Ropia...", "occurrences/CO_0046.json"),
        ("Occurrence of Currency Halve Ducaton ...", "occurrences/CO_0008.json"),
        ("Occurrence of Currency Goude Ducaat a...", "occurrences/CO_0040.json"),
        ("Occurrence of Currency Daalder at Jak...", "occurrences/CO_0016.json"),
        ("Occurrence of Currency Duiten at Banten", "occurrences/CO_0077.json"),
        ("Occurrence of Currency Koopere Duiten...", "occurrences/CO_0089.json"),
        ("Occurrence of Currency Keizerdaalder ...", "occurrences/CO_0023.json"),
        ("Occurrence of Currency Mariendaalder ...", "occurrences/CO_0024.json"),
    ],
    "conversion": [
        ("Conversion of Currency Cojang to Curr...", "conversion/UC_0073.json"),
        ("Conversion of Currency Tjajang to Cur...", "conversion/UC_0053.json"),
        ("Conversion of Currency Kwart Kan to C...", "conversion/UC_0044.json"),
        ("Conversion of Currency Kati to Curren...", "conversion/UC_0014.json"),
        ("Conversion of Currency Last to Curren...", "conversion/UC_0006.json"),
        ("Conversion of Currency Half musje to ...", "conversion/UC_0045.1.json"),
        ("Conversion of Currency Cojang to Curr...", "conversion/UC_0074.json"),
        ("Conversion of Currency Rijnlandse Roe...", "conversion/UC_0056.json"),
        ("Conversion of Currency Gantang to Cur...", "conversion/UC_0067.json"),
        ("Conversion of Currency Halve pint to ...", "conversion/UC_0043.json"),
    ],
    "concept": [
        ("Verf", "thesaurus/1ae80718-7cdb-4a23-942d-7a0b72033f2c.json"),
        ("Catechu", "thesaurus/2e988fa3-45bb-47b6-9b2f-67f0996fca4e.json"),
        ("expressed gender", "thesaurus/4db08b21-83a1-4a6c-8c56-2388f421d1ae.json"),
        ("plaatijzer", "thesaurus/64b0187f-56fe-4344-9909-a1108142b27b.json"),
        ("Pécun", "thesaurus/8e98c143-981f-44cb-aa8c-76de0f801cc6.json"),
        ("Thombohouder", "thesaurus/9849e4d2-86f6-48fc-a51b-5e364bbc2656.json"),
        ("fregat", "thesaurus/cba4b1a8-d4ce-424c-8a8f-91cfa1891b97.json"),
        ("mong", "thesaurus/d92361bd-6a77-412d-9068-5de09547f878.json"),
        ("Companheiras", "thesaurus/fc185757-6e8d-42aa-8323-31b6574a61ad.json"),
        ("Laypa Regiotags", "thesaurus/laypa.json"),
    ],
    "inventory": [
        ("Inventory 1465: 1690 DDDDD. Negende b...", "inventory/1465.json"),
        ("Inventory 2272: 1734. ZZZZZZ. Vijftie...", "inventory/2272.json"),
        ("Inventory 3877: 1791. Dertiende boek:...", "inventory/3877.json"),
        ("Inventory 4397: 1764 - 1766 [1766]. E...", "inventory/4397.json"),
        ("Inventory 8643: 1755 jan. 28 - sep. 27", "inventory/8643.json"),
        ("Inventory 9141: 1730 mei 8 - nov. 8", "inventory/9141.json"),
        ("Inventory 10775: 1755", "inventory/10775.json"),
        ("Inventory 3508: 1779. Negende boek: B...", "inventory/3508.json"),
        ("Inventory 8855: 1731 sep. 7 - nov. 23", "inventory/8855.json"),
        ("Inventory 10421: 1779 dec. 31", "inventory/10421.json"),
    ],
    "document": [
        (
            "Dingsdag den 9.:' December 1732.",
            "document/NL-HaNA_1.04.02_2212_0710-0721.json",
        ),
        (
            "Aen d'Ed: Heeren Mijn Heeren, De",
            "document/NL-HaNA_1.04.02_3990_0035-0039.json",
        ),
        ("Ik ondergesz Hendrik Elders", "document/NL-HaNA_1.04.02_3310_0893-0893.json"),
        ("Nagapatnam", "document/NL-HaNA_1.04.02_2116_0253-0264.json"),
        (
            "Heden den 16„e Janu„ Articulen",
            "document/NL-HaNA_1.04.02_9351_0817-0827.json",
        ),
        (
            "Document NL-HaNA_1.04.02_3887_0014-0015",
            "document/NL-HaNA_1.04.02_3887_0014-0015.json",
        ),
        (
            "Nederduijtse translaet missive door T...",
            "document/NL-HaNA_1.04.02_1554_0033-1485.json",
        ),
        (
            "Register der secreete papieren per 't...",
            "document/NL-HaNA_1.04.02_3785_0115-0116.json",
        ),
        (
            "Eijsch der behoeften van Batavia naar...",
            "document/e51f7d64-5861-40dc-9ec2-0522d8899c6c.json",
        ),
        (
            "Twee beedigde verklaringen door voorn...",
            "document/NL-HaNA_1.04.02_2143_0941-0951.json",
        ),
    ],
}


def create_http_session() -> Any:
    if not requests:
        return None
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=5, pool_maxsize=5)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP_SESSION = create_http_session()


def fetch_json(
    url_or_path: str,
    local_dirs: Optional[List[str]] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Fetch JSON-LD document from local filesystem or remote object store with retries."""
    if local_dirs:
        for loc in local_dirs:
            local_path = os.path.join(loc, url_or_path)
            if os.path.isfile(local_path):
                with open(local_path, "rb") as f:
                    content = f.read()
                    if content[:2] == b"\x1f\x8b":
                        content = gzip.decompress(content)
                    return json.loads(content.decode("utf-8"))

    full_url = (
        url_or_path
        if url_or_path.startswith("http")
        else f"{BASE_OBJECTSTORE_URL}{url_or_path}"
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            if HTTP_SESSION:
                resp = HTTP_SESSION.get(
                    full_url,
                    headers={"Accept-Encoding": "gzip"},
                    timeout=30,
                )
                resp.raise_for_status()
                content = resp.content
                if content[:2] == b"\x1f\x8b":
                    content = gzip.decompress(content)
                return json.loads(content.decode("utf-8"))
            else:
                import urllib.request

                req = urllib.request.Request(
                    full_url, headers={"Accept-Encoding": "gzip"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    if (
                        resp.info().get("Content-Encoding") == "gzip"
                        or data[:2] == b"\x1f\x8b"
                    ):
                        data = gzip.decompress(data)
                    return json.loads(data.decode("utf-8"))
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(1.5 * attempt)

    raise last_error or RuntimeError(f"Failed to fetch {full_url}")


def parse_yaml_spec(filepath: str) -> Dict[str, Dict[str, str]]:
    """
    Parse a YAML specification file.
    Returns a dictionary of sections: {section_name: {key: value, ...}}
    For flat specifications, section_name is the stem of the filename.
    """
    stem = os.path.splitext(os.path.basename(filepath))[0]
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Determine if file uses multi-section structure
    has_sections = any(
        re.match(r"^[a-zA-Z0-9_-]+:\s*$", line.strip())
        for line in lines
        if not line.strip().startswith("#")
    )

    sections: Dict[str, Dict[str, str]] = {}
    current_sec = stem if not has_sections else None

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        sec_match = re.match(r"^([a-zA-Z0-9_-]+):\s*$", line)
        if sec_match:
            current_sec = sec_match.group(1)
            sections[current_sec] = {}
            continue

        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if current_sec is None:
                current_sec = stem
            if current_sec not in sections:
                sections[current_sec] = {}
            sections[current_sec][k] = v
        else:
            raise ValueError(f"Line {line_no} in {filepath} is invalid: {line}")

    return sections


def verify_specification(
    spec_filename: str,
    spec_dir: str,
    local_dirs: Optional[List[str]] = None,
    verbose: bool = False,
    max_samples: int = 10,
) -> bool:
    """Validate a specification file and test all sections against real data."""
    filepath = os.path.join(spec_dir, spec_filename)
    stem = os.path.splitext(spec_filename)[0]
    print(f"\n========================================================")
    print(f"Verifying specification: {spec_filename}")
    print(f"========================================================")

    if not os.path.isfile(filepath):
        print(f"❌ File not found: {filepath}")
        return False

    try:
        sections = parse_yaml_spec(filepath)
    except Exception as e:
        print(f"❌ Failed to parse YAML structure: {e}")
        return False

    is_multi_source = len(sections) > 1
    if is_multi_source:
        print(
            f"  • Multi-source specification: {len(sections)} distinct JSON sources defined:"
        )
        for sec, items in sections.items():
            file_pattern = items.get("file", "NOT SPECIFIED")
            print(f"    - Section [{sec}]: file -> {file_pattern}")
    else:
        sec = next(iter(sections.keys()))
        file_pattern = sections[sec].get("file", "NOT SPECIFIED")
        print(f"  • Single-source specification: file -> {file_pattern}")

    # 1. Verify 'file' key presence
    missing_files = []
    for sec, items in sections.items():
        if "file" not in items:
            missing_files.append(sec)

    if missing_files:
        print(f"❌ FAILED: Missing 'file' definition in section(s): {missing_files}")
        return False
    else:
        print("  ✓ 'file' target source pattern specified for all sections.")

    # 2. Strict check: assert NO '_label' anywhere
    label_violations = []
    for sec, items in sections.items():
        for k, p in items.items():
            if k == "file":
                continue
            if "_label" in p:
                label_violations.append((sec, k, p))

    if label_violations:
        print("❌ FAILED: Found prohibited '_label' in specification paths:")
        for sec, k, p in label_violations:
            print(f"    [{sec}] {k}: {p}")
        return False
    else:
        print("  ✓ Strict check passed: No '_label' present in any path or filter.")

    # 3. Parse and compile all JSONPath expressions
    compiled_by_sec: Dict[str, Dict[str, Any]] = {}
    compile_errors = []
    total_paths = 0

    for sec, items in sections.items():
        compiled_by_sec[sec] = {}
        for k, p in items.items():
            if k == "file":
                continue
            total_paths += 1
            try:
                compiled = parse_jsonpath(p)
                compiled_by_sec[sec][k] = compiled
            except Exception as e:
                compile_errors.append((sec, k, p, str(e)))

    if compile_errors:
        print(
            f"❌ FAILED: {len(compile_errors)} JSONPath expressions have syntax errors:"
        )
        for sec, k, p, err in compile_errors:
            print(f"    [{sec}] {k}: {p} -> {err}")
        return False
    else:
        print(f"  ✓ All {total_paths} JSONPath expressions parsed successfully.")

    # 4. Live evaluation against sample JSON-LD records for each section
    for sec, compiled_paths in compiled_by_sec.items():
        # Find sample target for this section
        target_key = sec if sec in SECTION_SAMPLE_TARGETS else stem
        samples = SECTION_SAMPLE_TARGETS.get(target_key, [])
        file_pat = sections[sec].get("file")

        print(f"\n  Testing section [{sec}] (target file: {file_pat}):")
        if not samples:
            print(f"    ⚠ No sample target configured for section [{sec}].")
            continue

        for idx, (label, sample_rel) in enumerate(samples[:max_samples], start=1):
            try:
                doc = fetch_json(sample_rel, local_dirs=local_dirs)
            except Exception as e:
                print(
                    f"      ❌ [{idx}/{len(samples)}] Failed to fetch sample {sample_rel}: {e}"
                )
                return False

            matched_count = 0
            extracted = {}
            for k, expr in compiled_paths.items():
                res = [m.value for m in expr.find(doc)]
                if res:
                    matched_count += 1
                    extracted[k] = res

            print(
                f"    [{idx:2d}/{min(len(samples), max_samples)}] ✓ {label[:30]:30s} ({sample_rel}) -> matched {matched_count}/{len(compiled_paths)} fields"
            )
            if verbose or idx == 1:
                preview_keys = list(extracted.keys())[:4]
                for pk in preview_keys:
                    val = extracted[pk]
                    d_val = val[0] if len(val) == 1 else val[:3]
                    if isinstance(d_val, str) and len(d_val) > 60:
                        d_val = d_val[:57] + "..."
                    print(f"         • {pk:16}: {d_val}")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify JSONPath specifications against GLOBALISE catalog data."
    )
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument(
        "--spec-dir",
        default=script_dir,
        help="Directory containing the .yaml specifications (default: script dir)",
    )
    parser.add_argument(
        "--local-dir",
        default=None,
        help="Optional local output directory (e.g. data/output/s3) for cached fallbacks",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=10,
        help="Maximum samples to verify per section (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Display detailed match outputs for all samples",
    )
    parser.add_argument(
        "spec_files",
        nargs="*",
        help="Optional subset of specification files to test (e.g. polity.yaml)",
    )
    args = parser.parse_args()

    repo_s3_dir = os.path.abspath(os.path.join(script_dir, "..", "output", "s3"))
    local_dirs = []
    if args.local_dir:
        local_dirs.append(args.local_dir)
    if os.path.isdir(repo_s3_dir):
        local_dirs.append(repo_s3_dir)

    target_files = args.spec_files or sorted(
        [f for f in os.listdir(args.spec_dir) if f.endswith(".yaml")]
    )

    if not target_files:
        sys.exit(f"No .yaml files found in {args.spec_dir}")

    total_specs = len(target_files)
    successful = 0

    print("================================================================")
    print("GLOBALISE JSONPath Entity Specification Verification Suite")
    print(f"Spec directory: {args.spec_dir}")
    if local_dirs:
        print(f"Local fallback directories: {local_dirs}")
    print(f"Total specs to verify: {total_specs}")
    print("================================================================")

    for spec_file in target_files:
        ok = verify_specification(
            spec_file,
            spec_dir=args.spec_dir,
            local_dirs=local_dirs,
            verbose=args.verbose,
            max_samples=args.max_samples,
        )
        if ok:
            successful += 1

    print("\n================================================================")
    print(f"Summary: {successful}/{total_specs} specifications verified successfully.")
    print("================================================================")

    if successful != total_specs:
        sys.exit(1)


if __name__ == "__main__":
    main()
