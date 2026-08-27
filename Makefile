all: help

TAG = globalise-reference-data-pipeline
SHELL = /bin/bash

RED    = \033[1;31m
GREEN  = \033[1;32m
YELLOW = \033[1;33m
BLUE   = \033[1;34m
RESET  = \033[0m

# Load environment variables from .env file if present
-include .env
export

# Default parallel jobs: 6
NPROCS ?= 6
MAKEFLAGS += -j$(NPROCS)

PYTHON := uv run python
JAVA_OPTS ?= -Xmx4g
RUN_X3ML := JAVA_OPTS="$(JAVA_OPTS)" ./scripts/x3ml_runner.sh

# JSON formatting options (default: enabled gzip compression)
GZIP_FLAG ?= --gzipped
S3_FLAGS ?=

S3_DIR := data/output/s3

OLD_BASE := https?://digitaalerfgoed.poolparty.biz/globalise/
NEW_BASE := https://data.globalise.huygens.knaw.nl/hdl:20.500.14722/thesaurus:

#------------------------------------------------------------

.PHONY: build-all
build-all: organization place person polity ship measurement thesaurus catalog

.PHONY: organization
organization: $(S3_DIR)/.organization.stamp

.PHONY: place
place: $(S3_DIR)/.place.stamp

.PHONY: person
person: $(S3_DIR)/.person.stamp

.PHONY: polity
polity: $(S3_DIR)/.polity.stamp

.PHONY: ship
ship: $(S3_DIR)/.ship.stamp

.PHONY: measurement
measurement: $(S3_DIR)/.measurement.stamp

.PHONY: thesaurus
thesaurus: $(S3_DIR)/.thesaurus.stamp

.PHONY: catalog
catalog: $(S3_DIR)/.catalog.stamp

.PHONY: links
links:
	$(PYTHON) scripts/regenerate_links_data.py

#------------------------------------------------------------
# 1. Organization Pipeline
#------------------------------------------------------------
ORG_XLSX := $(wildcard data/input/organization/*.xlsx)

data/input/organization/csv/.stamp: $(ORG_XLSX)
	@mkdir -p data/input/organization/csv
	$(PYTHON) scripts/xlsx_to_csv.py "$<" data/input/organization/csv
	@for f in data/input/organization/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/organization/xml/.stamp: data/input/organization/csv/.stamp
	@mkdir -p data/input/organization/xml
	@for f in data/input/organization/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			stem=$$(basename "$$f" .csv); \
			$(PYTHON) scripts/csv_to_xml.py --skip-existing "$$f" "data/input/organization/xml/$${stem}.xml"; \
		fi; \
	done
	@touch $@

data/output/organization/rdf/.stamp: data/input/organization/xml/.stamp
	@mkdir -p data/output/organization/rdf
	$(RUN_X3ML) data/input/organization/xml persons_in_organization.xml data/mappings/organization/Mapping621.x3ml data/output/organization/rdf 621_membership
	$(RUN_X3ML) data/input/organization/xml classifications.xml data/mappings/organization/Mapping605.x3ml data/output/organization/rdf 605_classifications
	$(RUN_X3ML) data/input/organization/xml relations_between_units.xml data/mappings/organization/Mapping607.x3ml data/output/organization/rdf 607_relations
	$(RUN_X3ML) data/input/organization/xml locations.xml data/mappings/organization/Mapping608.x3ml data/output/organization/rdf 608_locations
	$(RUN_X3ML) data/input/organization/xml external_links.xml data/mappings/organization/Mapping609.x3ml data/output/organization/rdf 609_external
	$(RUN_X3ML) data/input/organization/xml labels.xml data/mappings/organization/Mapping610.x3ml data/output/organization/rdf 610_labels
	$(RUN_X3ML) data/input/organization/xml organization_overview.xml data/mappings/organization/Mapping611.x3ml data/output/organization/rdf 611_overview
	@touch $@

data/output/organization/organization.ttl: data/output/organization/rdf/.stamp
	$(PYTHON) scripts/convert_to_ttl.py organization data/output/organization/rdf $@

$(S3_DIR)/.organization.stamp: data/output/organization/organization.ttl
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/organization/organization.jsonld "http://www.cidoc-crm.org/cidoc-crm/E74_Group" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 2. Place Pipeline
#------------------------------------------------------------
PLACE_XLSX := data/input/place/GLOBALISE\ -\ Places\ in\ the\ Dutch\ East\ India\ Company\ Archives\ (1602-1799).xlsx

data/input/place/csv/.stamp: $(PLACE_XLSX)
	@mkdir -p data/input/place/csv
	$(PYTHON) scripts/xlsx_to_csv.py "$<" data/input/place/csv
	@for f in data/input/place/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/place/xml/.stamp: data/input/place/csv/.stamp
	@mkdir -p data/input/place/xml
	@for f in data/input/place/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			stem=$$(basename "$$f" .csv); \
			$(PYTHON) scripts/csv_to_xml.py --split-pipes --skip-existing "$$f" "data/input/place/xml/$${stem}.xml"; \
		fi; \
	done
	@touch $@

data/output/place/rdf/.stamp: data/input/place/xml/.stamp
	@mkdir -p data/output/place/rdf
	#$(RUN_X3ML) data/input/place/xml Sheet3_Places_Location_Detail.xml data/mappings/place/Mapping622.x3ml data/output/place/rdf 622_location
	#$(RUN_X3ML) data/input/place/xml Sheet4_Places_Labels_Detail.xml data/mappings/place/Mapping623.x3ml data/output/place/rdf 623_labels
	#$(RUN_X3ML) data/input/place/xml Sheet5_Places_Types_Detail.xml data/mappings/place/Mapping624.x3ml data/output/place/rdf 624_types
	#$(RUN_X3ML) data/input/place/xml Sheet6_Places_Regions_Detail.xml data/mappings/place/Mapping626.x3ml data/output/place/rdf 626_regions
	$(RUN_X3ML) data/input/place/xml places.xml data/mappings/place/Mapping622.x3ml data/output/place/rdf 622_location
	$(RUN_X3ML) data/input/place/xml labels.xml data/mappings/place/Mapping623.x3ml data/output/place/rdf 623_labels
	$(RUN_X3ML) data/input/place/xml types.xml data/mappings/place/Mapping624.x3ml data/output/place/rdf 624_types
	$(RUN_X3ML) data/input/place/xml place_relations.xml data/mappings/place/Mapping626.x3ml data/output/place/rdf 626_regions
	@touch $@

data/output/place/place.ttl: data/output/place/rdf/.stamp
	$(PYTHON) scripts/convert_to_ttl.py place data/output/place/rdf $@

$(S3_DIR)/.place.stamp: data/output/place/place.ttl
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/place/place.jsonld "http://www.cidoc-crm.org/cidoc-crm/E53_Place" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 3. Person Pipeline
#------------------------------------------------------------
PERSON_ZIP := $(wildcard data/input/person/persons_data_dataverse.zip)

data/input/person/csv/.stamp: $(PERSON_ZIP)
	@mkdir -p data/input/person/csv
	unzip -o -q "$<" "*.csv" -d data/input/person/csv
	@for f in data/input/person/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/person/xml/.stamp: data/input/person/csv/.stamp
	@mkdir -p data/input/person/xml
	$(PYTHON) scripts/csv_to_xml.py --person-chunking --skip-existing data/input/person/csv data/input/person/xml
	@touch $@

data/output/person/rdf/.stamp: data/input/person/xml/.stamp
	@mkdir -p data/output/person/rdf
	$(RUN_X3ML) data/input/person/xml persons.xml data/mappings/person/Mapping541.x3ml data/output/person/rdf 541_persons
	$(RUN_X3ML) data/input/person/xml statuses.xml data/mappings/person/Mapping532.x3ml data/output/person/rdf 532_statuses
	$(RUN_X3ML) data/input/person/xml relations.xml data/mappings/person/Mapping534.x3ml data/output/person/rdf 534_relations
	$(RUN_X3ML) data/input/person/xml locationRelations.xml data/mappings/person/Mapping535.x3ml data/output/person/rdf 535_location_relations
	$(RUN_X3ML) data/input/person/xml identities.xml data/mappings/person/Mapping536.x3ml data/output/person/rdf 536_identities
	$(RUN_X3ML) data/input/person/xml appellations.xml data/mappings/person/Mapping537.x3ml data/output/person/rdf 537_appellations
	$(RUN_X3ML) data/input/person/xml activeAs.xml data/mappings/person/Mapping538.x3ml data/output/person/rdf 538_active_as
	$(RUN_X3ML) data/input/person/xml externalReferences.xml data/mappings/person/Mapping540.x3ml data/output/person/rdf 540_external_references
	$(RUN_X3ML) data/input/person/xml events.xml data/mappings/person/Mapping596.x3ml data/output/person/rdf 596_events
	@touch $@

data/output/person/ttl/.stamp: data/output/person/rdf/.stamp
	@mkdir -p data/output/person/ttl
	$(PYTHON) scripts/convert_to_ttl.py person data/output/person/rdf data/output/person/ttl
	@touch $@

$(S3_DIR)/.person.stamp: data/output/person/ttl/.stamp
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity data/output/person/ttl $(S3_DIR) data/frames/person/person.jsonld "http://www.cidoc-crm.org/cidoc-crm/E21_Person" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 4. Polity Pipeline
#------------------------------------------------------------
POLITY_XLSX := data/input/polity/GLOBALISE-\ Polities\ Dataset.xlsx

data/input/polity/csv/.stamp: $(POLITY_XLSX)
	@mkdir -p data/input/polity/csv
	$(PYTHON) scripts/xlsx_to_csv.py "$<" data/input/polity/csv
	@for f in data/input/polity/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/polity/xml/.stamp: data/input/polity/csv/.stamp
	@mkdir -p data/input/polity/xml
	@for f in data/input/polity/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			stem=$$(basename "$$f" .csv); \
			$(PYTHON) scripts/csv_to_xml.py --skip-existing "$$f" "data/input/polity/xml/$${stem}.xml"; \
		fi; \
	done
	@touch $@

data/output/polity/rdf/.stamp: data/input/polity/xml/.stamp
	@mkdir -p data/output/polity/rdf
	$(RUN_X3ML) data/input/polity/xml Sheet_1_Polities.xml data/mappings/polity/Mapping411.x3ml data/output/polity/rdf 411_polities
	$(RUN_X3ML) data/input/polity/xml Sheet_2_Polity_Labels.xml data/mappings/polity/Mapping413.x3ml data/output/polity/rdf 413_polity_labels
	$(RUN_X3ML) data/input/polity/xml Sheet_3_Rulerships.xml data/mappings/polity/Mapping520.x3ml data/output/polity/rdf 520_rulerships
	$(RUN_X3ML) data/input/polity/xml Sheet_4_Rulership_Labels.xml data/mappings/polity/Mapping601.x3ml data/output/polity/rdf 601_rulership_labels
	$(RUN_X3ML) data/input/polity/xml Sheet_5_Rulers.xml data/mappings/polity/Mapping602.x3ml data/output/polity/rdf 602_rulers
	$(RUN_X3ML) data/input/polity/xml Sheet_7_Succession.xml data/mappings/polity/Mapping521.x3ml data/output/polity/rdf 521_succession
	@touch $@

data/output/polity/polity.ttl: data/output/polity/rdf/.stamp
	$(PYTHON) scripts/convert_to_ttl.py polity data/output/polity/rdf $@

$(S3_DIR)/.polity.stamp: data/output/polity/polity.ttl
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/polity/polity.jsonld "https://ontology.swissartresearch.net/aaao/ZE39_Polity" $(GZIP_FLAG) $(S3_FLAGS)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/polity/rulership.jsonld "https://ontology.swissartresearch.net/pwro/WE2_Sovereignty" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 5. Ship Pipeline
#------------------------------------------------------------
SHIP_XLSX := $(wildcard data/input/ship/*.xlsx)

data/input/ship/csv/.stamp: $(SHIP_XLSX)
	@mkdir -p data/input/ship/csv
	$(PYTHON) scripts/xlsx_to_csv.py "$<" data/input/ship/csv
	@for f in data/input/ship/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/ship/xml/.stamp: data/input/ship/csv/.stamp
	@mkdir -p data/input/ship/xml
	@for f in data/input/ship/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			stem=$$(basename "$$f" .csv); \
			$(PYTHON) scripts/csv_to_xml.py --skip-existing "$$f" "data/input/ship/xml/$${stem}.xml"; \
		fi; \
	done
	@touch $@

data/output/ship/rdf/.stamp: data/input/ship/xml/.stamp
	@mkdir -p data/output/ship/rdf
	$(RUN_X3ML) data/input/ship/xml ships.xml data/mappings/ship/Mapping519.x3ml data/output/ship/rdf 519_ships
	$(RUN_X3ML) data/input/ship/xml ship_links.xml data/mappings/ship/Mapping544.x3ml data/output/ship/rdf 544_links
	$(RUN_X3ML) data/input/ship/xml ship_labels.xml data/mappings/ship/Mapping545.x3ml data/output/ship/rdf 545_labels
	$(RUN_X3ML) data/input/ship/xml translocations.xml data/mappings/ship/Mapping546.x3ml data/output/ship/rdf 546_translocations
	$(RUN_X3ML) data/input/ship/xml ship_existence.xml data/mappings/ship/Mapping548.x3ml data/output/ship/rdf 548_existence
	$(RUN_X3ML) data/input/ship/xml ship_ownership.xml data/mappings/ship/Mapping550.x3ml data/output/ship/rdf 550_ownership
	$(RUN_X3ML) data/input/ship/xml ship_transaction.xml data/mappings/ship/Mapping581.x3ml data/output/ship/rdf 581_transactions
	$(RUN_X3ML) data/input/ship/xml translocation_links.xml data/mappings/ship/Mapping585.x3ml data/output/ship/rdf 585_translocation_links
	@touch $@

data/output/ship/ship.ttl: data/output/ship/rdf/.stamp
	$(PYTHON) scripts/convert_to_ttl.py ship data/output/ship/rdf $@

$(S3_DIR)/.ship.stamp: data/output/ship/ship.ttl
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/ship/ship.jsonld "http://www.cidoc-crm.org/cidoc-crm/E22_Human-Made_Object" $(GZIP_FLAG) $(S3_FLAGS)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/ship/voyage.jsonld "https://ontology.swissartresearch.net/pwro/WE7_Voyage" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 6. Measurement Pipeline
#------------------------------------------------------------
MEAS_XLSX := $(wildcard data/input/measurement/*.xlsx)

data/input/measurement/csv/.stamp: $(MEAS_XLSX)
	@mkdir -p data/input/measurement/csv
	$(PYTHON) scripts/xlsx_to_csv.py "$<" data/input/measurement/csv
	@for f in data/input/measurement/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			sed -E -i "s|$(OLD_BASE)|$(NEW_BASE)|g" "$$f"; \
		fi; \
	done
	@touch $@

data/input/measurement/xml/.stamp: data/input/measurement/csv/.stamp
	@mkdir -p data/input/measurement/xml
	@for f in data/input/measurement/csv/*.csv; do \
		if [ -f "$$f" ]; then \
			stem=$$(basename "$$f" .csv); \
			$(PYTHON) scripts/csv_to_xml.py --skip-existing "$$f" "data/input/measurement/xml/$${stem}.xml"; \
		fi; \
	done
	@touch $@

data/output/measurement/rdf/.stamp: data/input/measurement/xml/.stamp
	@mkdir -p data/output/measurement/rdf
	$(RUN_X3ML) data/input/measurement/xml unit_conversion_sample.xml data/mappings/measurement/Mapping618.x3ml data/output/measurement/rdf 618_conversion
	$(RUN_X3ML) data/input/measurement/xml currency_occurrences.xml data/mappings/measurement/Mapping619.x3ml data/output/measurement/rdf 619_currencies
	$(RUN_X3ML) data/input/measurement/xml currency_occurrences.xml data/mappings/measurement/Mapping620.x3ml data/output/measurement/rdf 620_occurrences
	@touch $@

data/output/measurement/measurement.ttl: data/output/measurement/rdf/.stamp
	$(PYTHON) scripts/convert_to_ttl.py measurement data/output/measurement/rdf $@

$(S3_DIR)/.measurement.stamp: data/output/measurement/measurement.ttl
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/measurement/conversion.jsonld "https://w3id.org/globalise/ontology/G1_Financial_Exchange" $(GZIP_FLAG) $(S3_FLAGS)
	$(PYTHON) scripts/convert_to_json.py entity $< $(S3_DIR) data/frames/measurement/occurrence.jsonld "http://www.cidoc-crm.org/cidoc-crm/E5_Event" $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# 7. Thesaurus Pipeline
#------------------------------------------------------------
THESAURUS_TRIG := data/input/concept/thesaurus.trig

$(S3_DIR)/.thesaurus.stamp: $(THESAURUS_TRIG)
	@mkdir -p $(S3_DIR) data/output/concept
	sed -E "s|$(OLD_BASE)|$(NEW_BASE)|g" "$<" > data/output/concept/thesaurus.trig
	$(PYTHON) scripts/convert_to_json.py thesaurus data/output/concept/thesaurus.trig $(S3_DIR) $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

$(S3_DIR)/.catalog.stamp:
	@mkdir -p $(S3_DIR)
	$(PYTHON) scripts/convert_to_json.py catalog $(S3_DIR) $(GZIP_FLAG) $(S3_FLAGS)
	@touch $@

#------------------------------------------------------------
# Housekeeping & Help
#------------------------------------------------------------
.PHONY: test
test:
	$(PYTHON) -m doctest scripts/utils.py
	$(PYTHON) -m doctest scripts/convert_to_ttl.py
	$(PYTHON) -m doctest scripts/csv_to_xml.py
	$(PYTHON) -m doctest scripts/xlsx_to_csv.py
	$(PYTHON) -m doctest scripts/convert_to_json.py

.PHONY: clean clean-json clean-ttl clean-rdf clean-xml clean-csv \
        clean-organization clean-place clean-person clean-polity clean-ship clean-measurement clean-thesaurus clean-links

clean: clean-json clean-ttl clean-rdf clean-xml clean-csv clean-links
	rm -f .cache.sqlite

clean-links:
	rm -f data/input/links_data.parquet

clean-organization:
	rm -rf data/input/organization/csv data/input/organization/csv/.stamp
	rm -rf data/input/organization/xml data/input/organization/xml/.stamp
	rm -rf data/output/organization/rdf data/output/organization/rdf/.stamp data/output/organization/rdf/.*.stamp
	rm -rf data/output/organization/organization.ttl
	rm -rf $(S3_DIR)/.organization.stamp $(S3_DIR)/organization*.jsonld.gz $(S3_DIR)/organization*.jsonld

clean-place:
	rm -rf data/input/place/csv data/input/place/csv/.stamp
	rm -rf data/input/place/xml data/input/place/xml/.stamp
	rm -rf data/output/place/rdf data/output/place/rdf/.stamp data/output/place/rdf/.*.stamp
	rm -rf data/output/place/place.ttl
	rm -rf $(S3_DIR)/.place.stamp $(S3_DIR)/place*.jsonld.gz $(S3_DIR)/place*.jsonld

clean-person:
	rm -rf data/input/person/csv data/input/person/csv/.stamp
	rm -rf data/input/person/xml data/input/person/xml/.stamp
	rm -rf data/output/person/rdf data/output/person/rdf/.stamp data/output/person/rdf/.*.stamp
	rm -rf data/output/person/ttl data/output/person/ttl/.stamp data/output/person/ttl/.*.stamp
	rm -rf data/output/person/person.ttl
	rm -rf $(S3_DIR)/.person.stamp $(S3_DIR)/person*.jsonld.gz $(S3_DIR)/person*.jsonld

clean-polity:
	rm -rf data/input/polity/csv data/input/polity/csv/.stamp
	rm -rf data/input/polity/xml data/input/polity/xml/.stamp
	rm -rf data/output/polity/rdf data/output/polity/rdf/.stamp data/output/polity/rdf/.*.stamp
	rm -rf data/output/polity/polity.ttl
	rm -rf $(S3_DIR)/.polity.stamp $(S3_DIR)/polity*.jsonld.gz $(S3_DIR)/polity*.jsonld $(S3_DIR)/rulership*.jsonld.gz $(S3_DIR)/rulership*.jsonld

clean-ship:
	rm -rf data/input/ship/csv data/input/ship/csv/.stamp
	rm -rf data/input/ship/xml data/input/ship/xml/.stamp
	rm -rf data/output/ship/rdf data/output/ship/rdf/.stamp data/output/ship/rdf/.*.stamp
	rm -rf data/output/ship/ship.ttl
	rm -rf $(S3_DIR)/.ship.stamp $(S3_DIR)/ship*.jsonld.gz $(S3_DIR)/ship*.jsonld $(S3_DIR)/voyage*.jsonld.gz $(S3_DIR)/voyage*.jsonld

clean-measurement:
	rm -rf data/input/measurement/csv data/input/measurement/csv/.stamp
	rm -rf data/input/measurement/xml data/input/measurement/xml/.stamp
	rm -rf data/output/measurement/rdf data/output/measurement/rdf/.stamp data/output/measurement/rdf/.*.stamp
	rm -rf data/output/measurement/measurement.ttl
	rm -rf $(S3_DIR)/.measurement.stamp $(S3_DIR)/conversion*.jsonld.gz $(S3_DIR)/conversion*.jsonld $(S3_DIR)/occurrence*.jsonld.gz $(S3_DIR)/occurrence*.jsonld

clean-thesaurus:
	rm -rf data/output/concept $(S3_DIR)/.thesaurus.stamp $(S3_DIR)/concept*.jsonld.gz $(S3_DIR)/concept*.jsonld

clean-json:
	rm -rf $(S3_DIR)/* $(S3_DIR)/.*.stamp data/output/concept

clean-ttl:
	rm -rf data/output/*/*.ttl

clean-rdf:
	rm -rf data/output/*/rdf data/output/*/rdf/.stamp data/output/*/rdf/.*.stamp

clean-xml:
	rm -rf data/input/*/xml data/input/*/xml/.stamp

clean-csv:
	rm -rf data/input/*/csv data/input/*/csv/.stamp

.PHONY: help
help:
	@echo -e "make-tools for $(GREEN)$(TAG)$(RESET)"
	@echo
	@echo -e "Please use \`$(YELLOW)make <target>$(RESET)', where $(YELLOW)<target>$(RESET) is one of:"
	@echo -e "  $(BLUE)build-all$(RESET)                  - to run full ETL pipeline for all entities"
	@echo -e "  $(BLUE)organization$(RESET)               - to run organization ETL pipeline"
	@echo -e "  $(BLUE)place$(RESET)                      - to run place ETL pipeline"
	@echo -e "  $(BLUE)person$(RESET)                     - to run person ETL pipeline"
	@echo -e "  $(BLUE)polity$(RESET)                     - to run polity ETL pipeline"
	@echo -e "  $(BLUE)ship$(RESET)                       - to run ship ETL pipeline"
	@echo -e "  $(BLUE)measurement$(RESET)                - to run measurement ETL pipeline"
	@echo -e "  $(BLUE)thesaurus$(RESET)                  - to run thesaurus ETL pipeline"
	@echo -e "  $(BLUE)catalog$(RESET)                    - to generate Hydra catalog index"
	@echo -e "  $(BLUE)links$(RESET)                      - to regenerate data/input/links_data.parquet from Object Store"
	@echo
	@echo -e "  $(BLUE)clean-organization$(RESET)          - to remove intermediate files & output for organization"
	@echo -e "  $(BLUE)clean-place$(RESET)                 - to remove intermediate files & output for place"
	@echo -e "  $(BLUE)clean-person$(RESET)                - to remove intermediate files & output for person"
	@echo -e "  $(BLUE)clean-polity$(RESET)                - to remove intermediate files & output for polity"
	@echo -e "  $(BLUE)clean-ship$(RESET)                  - to remove intermediate files & output for ship"
	@echo -e "  $(BLUE)clean-measurement$(RESET)           - to remove intermediate files & output for measurement"
	@echo -e "  $(BLUE)clean-thesaurus$(RESET)             - to remove intermediate files & output for thesaurus"
	@echo -e "  $(BLUE)clean-links$(RESET)                 - to remove generated links parquet data"
	@echo
	@echo -e "  $(BLUE)test$(RESET)                       - to run doctests across all python scripts"
	@echo -e "  $(BLUE)clean$(RESET)                      - to remove all generated intermediate files and outputs"
	@echo -e "  $(BLUE)clean-json$(RESET)                 - to remove only framed JSON outputs and S3 stamps"
	@echo -e "  $(BLUE)clean-ttl$(RESET)                  - to remove only merged Turtle (.ttl) files"
	@echo -e "  $(BLUE)clean-rdf$(RESET)                  - to remove only generated RDF/XML files and stamps"
	@echo -e "  $(BLUE)clean-xml$(RESET)                  - to remove only generated XML files and stamps"
	@echo -e "  $(BLUE)clean-csv$(RESET)                  - to remove only extracted CSV files and stamps"
	@echo
	@echo -e "Configuration parameter overrides:"
	@echo -e "  $(YELLOW)NPROCS=N$(RESET)                   - Parallel Make jobs (default: $(NPROCS))"
	@echo -e "  $(YELLOW)JAVA_OPTS=opts$(RESET)             - Memory flags for X3ML Java JVM (default: $(JAVA_OPTS))"
	@echo -e "  $(YELLOW)GZIP_FLAG=flag$(RESET)             - Gzip flag (default: --gzipped, use GZIP_FLAG=\"\" for raw JSON)"
	@echo -e "  $(YELLOW)S3_BUCKET=name$(RESET)             - Direct upload to S3 bucket (or set in .env file)"
