#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
xml_dir="$1"
input_file="$2"
mapping_file="$3"
rdf_dir="$4"
output_base="$5"

JAR="${JAR:-$repo_root/scripts/x3ml/x3ml.jar}"
GEN="${GEN:-$repo_root/scripts/x3ml/generators/takin_condense_generators_globalise.xml}"
JAVA_OPTS="${JAVA_OPTS:--Xmx4g}"
NPROCS="${NPROCS:-6}"

mkdir -p "$rdf_dir"

input_stem="${input_file%.xml}"

shopt -s nullglob
chunk_files=("$xml_dir/${input_stem}"_part*.xml)
shopt -u nullglob

run_x3ml() {
  local in_file="$1"
  local out_file="$2"
  if [[ -s "$out_file" ]]; then
    echo "Skipping '$out_file' (RDF output already exists)."
  else
    java $JAVA_OPTS -jar "$JAR" -i "$in_file" -p "$GEN" --x3ml "$mapping_file" -o "$out_file" -u 16 -f text/turtle
  fi
}

if [[ "${#chunk_files[@]}" -gt 0 ]]; then
  for chunk_file in "${chunk_files[@]}"; do
    chunk_suffix="${chunk_file##*_part}"
    chunk_suffix="${chunk_suffix%.xml}"
    target_rdf="$rdf_dir/${output_base}_part${chunk_suffix}.ttl"
    run_x3ml "$chunk_file" "$target_rdf" &

    while [[ $(jobs -r -p | wc -l) -ge "$NPROCS" ]]; do
      sleep 0.1
    done
  done
  wait
elif [[ -f "$xml_dir/$input_file" ]]; then
  target_rdf="$rdf_dir/${output_base}.ttl"
  run_x3ml "$xml_dir/$input_file" "$target_rdf"
else
  echo "Warning: XML input '$xml_dir/$input_file' not found; skipping mapping '$mapping_file'." >&2
fi
