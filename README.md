# Data Processing & Pipeline

This repository converts dataset workbooks (Excel/CSV/TriG) into RDF knowledge graphs and framed JSON-LD files ready for S3 object storage upload.

---

## Running with `make` (Recommended)

The pipeline is managed via a dependency-driven [`Makefile`](Makefile).

### Quickstart

```bash
# Show help & target menu
make

# Run full pipeline for all entities & thesaurus
make build-all

# Run specific entity pipelines
make place
make person
make organization
make polity
make ship
make measurement
make thesaurus

# Run doctests across all Python scripts
make test

# Dry-run to preview execution commands
make -n all

# Stage-specific clean targets
make clean-json   # Remove only framed JSON files and stamps
make clean-ttl    # Remove only merged Turtle files
make clean-rdf    # Remove only generated RDF/XML files
make clean-xml    # Remove only intermediate XML files
make clean-csv    # Remove only extracted CSV files

# Clean all intermediate outputs and stamps
make clean
```

---

## Execution Examples: Writing Locally vs. Uploading to S3

### Example 1: Local Generation Only (Default)

When no `S3_BUCKET` variable or `--s3-bucket` flag is specified, running Make generates all framed `.jsonld.gz` files **locally** under `data/output/s3/`:

```bash
# Write all entities & thesaurus locally to data/output/s3/ (gzipped by default)
make all

# Write uncompressed JSON files
make GZIP_FLAG="" all

# Write only places locally
make place
```

---

### Example 2: Uploading Directly to Object Store (S3)

To upload generated JSON-LD files directly to your Object Store during generation, use one of the following methods:

#### Method A: Using a `.env` file (Recommended)

Create a `.env` file in the root directory (automatically loaded by Make):

```bash
# .env
S3_BUCKET=globalise-data
S3_PREFIX=objects
S3_ENDPOINT_URL=https://objectstore.surf.nl
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
```

Then simply run:

```bash
make all
```

#### Method B: Passing S3 variables directly on CLI

```bash
make S3_BUCKET=globalise-data S3_PREFIX=objects/ all
```

#### Method C: Passing custom S3 CLI flags

```bash
make S3_FLAGS="--s3-bucket globalise-data --s3-prefix objects/ --s3-acl public-read" all
```

---

## Parallelization & Memory Configuration

To prevent Out-Of-Memory (OOM) errors during heavy Java X3ML transformations and Python RDF graph parsing:

- **Default Parallel Jobs (`NPROCS`):** Defaults to `2` concurrent jobs (`-j2`).
- **Default JVM Memory (`JAVA_OPTS`):** Defaults to `-Xmx4g` per X3ML process.

You can customize these on the command line based on your system hardware:

```bash
# Example: Using 4 parallel jobs with 4GB RAM per Java process
make NPROCS=4 JAVA_OPTS="-Xmx4g" all
```

---

## Manual S3 Upload (Alternative)

If you generate files locally without direct S3 upload, you can sync the output folder manually afterwards:

```bash
aws s3 sync data/output/s3/ s3://globalise-data/objects/ --acl=public-read --content-encoding gzip
```

---
