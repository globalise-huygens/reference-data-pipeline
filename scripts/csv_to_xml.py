#!/usr/bin/env python3
"""
CSV to XML Converter matching X3ML input structure.
"""

import argparse
import csv
import os
from typing import TextIO
from xml.sax.saxutils import escape

from utils import xml_element_name

DEFAULT_ROWS_PER_FILE = 10000


def split_pipe_separated_values(value: str) -> list[str]:
    """
    Split a cell value on pipe separators while discarding empty entries.

    Examples:
        >>> split_pipe_separated_values('alpha|beta')
        ['alpha', 'beta']
        >>> split_pipe_separated_values('alpha | beta')
        ['alpha', 'beta']
        >>> split_pipe_separated_values('alpha')
        ['alpha']
    """
    return [part.strip() for part in value.split("|") if part.strip()]


def expand_row_by_pipe_values(
    row: dict[str, str], ordered_columns: list[str]
) -> list[dict[str, str]]:
    """
    Expand a row into aligned sub-rows when multiple columns contain pipe-delimited values.

    Example:
        >>> expand_row_by_pipe_values({
        ...     'source': 'a|b|c',
        ...     'page': '1|2|3',
        ...     'label': 'x',
        ... }, ['source', 'page', 'label'])
        [{'source': 'a', 'page': '1', 'label': 'x'}, {'source': 'b', 'page': '2', 'label': 'x'}, {'source': 'c', 'page': '3', 'label': 'x'}]
    """
    split_columns = [
        col for col in ordered_columns if "|" in (row.get(col) or "").strip()
    ]

    if not split_columns:
        return [row]

    split_values = {
        col: split_pipe_separated_values((row.get(col) or "").strip())
        for col in split_columns
    }
    max_length = max(len(values) for values in split_values.values())
    if max_length == 0:
        return [row]

    expanded_rows: list[dict[str, str]] = []
    for idx in range(max_length):
        expanded = {col: (row.get(col) or "").strip() for col in ordered_columns}
        for col in split_columns:
            expanded[col] = (
                split_values[col][idx] if idx < len(split_values[col]) else ""
            )
        expanded_rows.append(expanded)
    return expanded_rows


def parse_args(args_list: list[str] | None = None) -> argparse.Namespace:
    """
    Parse CLI arguments for CSV to XML conversion.

    Args:
        args_list (list[str], optional): CLI arguments list for testing. Defaults to None.

    Returns:
        argparse.Namespace: Parsed command-line arguments.

    Examples:
        >>> parse_args(["input.csv"]).input_csv
        'input.csv'
    """
    parser = argparse.ArgumentParser(
        description="Convert a CSV file or person CSV directory to XML with item-per-row structure."
    )
    parser.add_argument("input_csv")
    parser.add_argument("output_xml", nargs="?")
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=DEFAULT_ROWS_PER_FILE,
        help=(
            "Maximum number of CSV rows to include in each XML file "
            f"(default: {DEFAULT_ROWS_PER_FILE})."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip conversion if output XML file already exists.",
    )
    parser.add_argument(
        "--person-chunking",
        action="store_true",
        help="Chunk person CSVs by person ID from persons.csv across all person CSV files.",
    )
    parser.add_argument(
        "--split-pipes",
        action="store_true",
        help="Treat a pipe character as a value separator and emit repeated XML elements for each item.",
    )
    return parser.parse_args(args_list)


def build_chunk_path(output_path: str, chunk_index: int) -> str:
    """
    Build output XML filepath for a given chunk index.

    Args:
        output_path (str): Base output XML path.
        chunk_index (int): 1-based index of the chunk file.

    Returns:
        str: Formatted chunk filepath.

    Examples:
        >>> build_chunk_path("data/out.xml", 1)
        'data/out.xml'
        >>> build_chunk_path("data/out.xml", 2)
        'data/out_part0002.xml'
    """
    if chunk_index == 1:
        return output_path
    base, ext = os.path.splitext(output_path)
    return f"{base}_part{chunk_index:04d}{ext}"


def build_numbered_chunk_path(output_path: str, chunk_index: int) -> str:
    """
    Build numbered output path for chunk renaming.

    Args:
        output_path (str): Base output XML path.
        chunk_index (int): 1-based index of the chunk file.

    Returns:
        str: Formatted chunk filepath with part number suffix.

    Examples:
        >>> build_numbered_chunk_path("data/out.xml", 1)
        'data/out_part0001.xml'
    """
    base, ext = os.path.splitext(output_path)
    return f"{base}_part{chunk_index:04d}{ext}"


def write_xml_header(handle: TextIO) -> None:
    """
    Write standard XML declaration header and opening root tag.

    Args:
        handle (TextIO): Open text file handle.

    Examples:
        >>> import io
        >>> h = io.StringIO()
        >>> write_xml_header(h)
        >>> h.getvalue()
        '<?xml version="1.0" ?>\\n<data>\\n'
    """
    handle.write('<?xml version="1.0" ?>\n')
    handle.write("<data>\n")


def write_xml_footer(handle: TextIO) -> None:
    """
    Write closing tag for XML root element.

    Args:
        handle (TextIO): Open text file handle.

    Examples:
        >>> import io
        >>> h = io.StringIO()
        >>> write_xml_footer(h)
        >>> h.getvalue()
        '</data>\\n'
    """
    handle.write("</data>\n")


def write_item(
    handle: TextIO,
    row: dict[str, str],
    ordered_columns: list[str],
    elem_tags: list[tuple[str, str]] | None = None,
    split_pipe_values: bool = False,
) -> None:
    """
    Write a single CSV row dictionary as an XML item element.

    Args:
        handle (TextIO): Open text file handle.
        row (dict[str, str]): Row data mapping column names to values.
        ordered_columns (list[str]): Ordered list of column header names.
        elem_tags (list[tuple[str, str]], optional): Pre-computed (column, xml_element_name) pairs.
        split_pipe_values (bool): Whether to treat pipe-separated values as repeated XML values.

    Examples:
        >>> import io
        >>> h = io.StringIO()
        >>> write_item(h, {"name": "Test & Demo"}, ["name"])
        >>> h.getvalue()
        '  <item>\\n    <name>Test &amp; Demo</name>\\n  </item>\\n'
        >>> h = io.StringIO()
        >>> write_item(h, {"name": "alpha | beta"}, ["name"], split_pipe_values=True)
        >>> h.getvalue()
        '  <item>\\n    <name>alpha</name>\\n  </item>\\n  <item>\\n    <name>beta</name>\\n  </item>\\n'
    """

    if split_pipe_values:
        expanded_rows = expand_row_by_pipe_values(row, ordered_columns)
        if len(expanded_rows) > 1:
            for expanded_row in expanded_rows:
                handle.write("  <item>\n")
                if elem_tags is None:
                    elem_tags = [
                        (col, xml_element_name(col)) for col in ordered_columns
                    ]
                for col, element_name in elem_tags:
                    value = (expanded_row.get(col) or "").strip()
                    if value:
                        handle.write(
                            f"    <{element_name}>{escape(value)}</{element_name}>\n"
                        )
                    else:
                        handle.write(f"    <{element_name}></{element_name}>\n")
                handle.write("  </item>\n")
            return

    handle.write("  <item>\n")
    if elem_tags is None:
        elem_tags = [(col, xml_element_name(col)) for col in ordered_columns]
    for col, element_name in elem_tags:
        value = (row.get(col) or "").strip()
        if value:
            handle.write(f"    <{element_name}>{escape(value)}</{element_name}>\n")
        else:
            handle.write(f"    <{element_name}></{element_name}>\n")
    handle.write("  </item>\n")


def csv_to_xml(
    input_path: str,
    output_path: str,
    rows_per_file: int,
    split_pipe_values: bool = False,
) -> None:
    """
    Convert CSV file to XML items, splitting into multiple files if row count exceeds chunk limit.

    Args:
        input_path (str): Input CSV filepath.
        output_path (str): Target XML output path.
        rows_per_file (int): Maximum rows per XML chunk file.
        split_pipe_values (bool): Whether to split pipe-delimited cell values into repeated XML elements.

    Raises:
        ValueError: If rows_per_file is non-positive or CSV has no headers.
    """
    if rows_per_file <= 0:
        raise ValueError("--rows-per-file must be a positive integer.")

    output_paths: list[str] = []
    total_rows = 0
    rows_in_current_file = 0
    chunk_index = 0
    current_handle: TextIO | None = None

    with open(input_path, "r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames

        if not headers:
            raise ValueError("CSV file has no headers.")

        ordered = list(headers)
        elem_tags = [(col, xml_element_name(col)) for col in ordered]

        for row in reader:
            if current_handle is None or rows_in_current_file >= rows_per_file:
                if current_handle is not None:
                    write_xml_footer(current_handle)
                    current_handle.close()
                    if chunk_index == 1:
                        first_numbered_chunk = build_numbered_chunk_path(output_path, 1)
                        os.replace(output_paths[0], first_numbered_chunk)
                        output_paths[0] = first_numbered_chunk
                chunk_index += 1
                chunk_path = build_chunk_path(output_path, chunk_index)
                output_paths.append(chunk_path)
                current_handle = open(chunk_path, "w", encoding="utf-8")
                write_xml_header(current_handle)
                rows_in_current_file = 0

            write_item(
                current_handle,
                row,
                ordered,
                elem_tags,
                split_pipe_values=split_pipe_values,
            )
            rows_in_current_file += 1
            total_rows += 1

    if current_handle is None:
        with open(output_path, "w", encoding="utf-8") as empty_handle:
            write_xml_header(empty_handle)
            write_xml_footer(empty_handle)
        output_paths.append(output_path)
    else:
        write_xml_footer(current_handle)
        current_handle.close()

    if len(output_paths) == 1:
        print(f"Converted {total_rows} rows from '{input_path}' to '{output_paths[0]}'")
    else:
        print(
            f"Converted {total_rows} rows from '{input_path}' "
            f"into {len(output_paths)} XML files: "
            f"'{output_paths[0]}' ... '{output_paths[-1]}'"
        )


def person_csvs_to_xml(
    csv_dir: str,
    xml_dir: str,
    rows_per_file: int = DEFAULT_ROWS_PER_FILE,
    skip_existing: bool = False,
    split_pipe_values: bool = False,
) -> None:
    """
    Convert all person CSV files to XML chunks partitioned by person identifier from persons.csv.

    Args:
        csv_dir (str): Input directory containing person CSV files.
        xml_dir (str): Target output directory for XML chunk files.
        rows_per_file (int): Number of persons per XML chunk file.
        skip_existing (bool): Skip conversion if target XML files already exist.
        split_pipe_values (bool): Whether to split pipe-delimited cell values into repeated XML elements.
    """
    persons_csv = os.path.join(csv_dir, "persons.csv")
    if not os.path.isfile(persons_csv):
        raise ValueError(f"persons.csv not found in directory '{csv_dir}'")

    os.makedirs(xml_dir, exist_ok=True)

    first_chunk = os.path.join(xml_dir, "persons_part0001.xml")
    if skip_existing and os.path.exists(first_chunk):
        print(
            f"Skipping person CSV conversion (XML chunks in '{xml_dir}' already exist)."
        )
        return

    uri_to_chunk: dict[str, int] = {}
    with open(persons_csv, "r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            uri_to_chunk[row["URI"]] = (idx // rows_per_file) + 1

    num_chunks = max(uri_to_chunk.values()) if uri_to_chunk else 1

    csv_files = sorted(
        [os.path.join(csv_dir, f) for f in os.listdir(csv_dir) if f.endswith(".csv")]
    )

    for csv_path in csv_files:
        stem = os.path.splitext(os.path.basename(csv_path))[0]
        handles = []
        for c in range(1, num_chunks + 1):
            chunk_file = os.path.join(xml_dir, f"{stem}_part{c:04d}.xml")
            h = open(chunk_file, "w", encoding="utf-8")
            write_xml_header(h)
            handles.append(h)

        total_rows = 0
        with open(csv_path, "r", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            ordered = list(reader.fieldnames or [])
            elem_tags = [(col, xml_element_name(col)) for col in ordered]

            for row in reader:
                uri = row.get("URI", "")
                c_idx = uri_to_chunk.get(uri, 1)
                expanded_rows = (
                    expand_row_by_pipe_values(row, ordered)
                    if split_pipe_values
                    else [row]
                )
                for expanded_row in expanded_rows:
                    h = handles[c_idx - 1]
                    h.write("  <item>\n")
                    for col, elem in elem_tags:
                        val = (expanded_row.get(col) or "").strip()
                        if val:
                            h.write(f"    <{elem}>{escape(val)}</{elem}>\n")
                        else:
                            h.write(f"    <{elem}></{elem}>\n")
                    h.write("  </item>\n")
                    total_rows += 1

        for h in handles:
            write_xml_footer(h)
            h.close()

        print(
            f"Converted {total_rows} rows from '{stem}.csv' "
            f"into {num_chunks} person XML chunks."
        )


def main() -> int:
    """
    Execute main entry point for CSV to XML conversion.

    Returns:
        int: Exit status code (0 for success, 1 for error).
    """
    args = parse_args()

    if args.person_chunking:
        csv_dir = os.path.abspath(os.path.expanduser(args.input_csv))
        xml_dir = (
            os.path.abspath(os.path.expanduser(args.output_xml))
            if args.output_xml
            else os.path.join(os.path.dirname(csv_dir), "xml")
        )
        try:
            person_csvs_to_xml(
                csv_dir,
                xml_dir,
                args.rows_per_file,
                args.skip_existing,
                split_pipe_values=args.split_pipes,
            )
        except ValueError as err:
            print(f"Error: {err}")
            return 1
        return 0

    input_path = os.path.abspath(os.path.expanduser(args.input_csv))

    if not os.path.exists(input_path):
        print(f"Error: File '{input_path}' not found.")
        return 1

    output_path = (
        os.path.abspath(os.path.expanduser(args.output_xml))
        if args.output_xml
        else os.path.splitext(input_path)[0] + ".xml"
    )

    if args.skip_existing:
        first_chunk = build_numbered_chunk_path(output_path, 1)
        if os.path.exists(output_path) or os.path.exists(first_chunk):
            print(f"Skipping '{input_path}' (XML output already exists).")
            return 0

    try:
        csv_to_xml(input_path, output_path, args.rows_per_file, args.split_pipes)
    except ValueError as err:
        print(f"Error: {err}")
        return 1

    return 0


if __name__ == "__main__":
    import doctest

    doctest.testmod()
    raise SystemExit(main())
