---
schema_version: 1
congress: 119
chamber: house
committee: Committee on Example Affairs
committee_type: standing
source_url: https://example.house.gov/committee/jurisdiction
retrieved_at: 2026-08-15T10:00:00Z
source_metadata_status: complete
source_files:
  - data/committee_matcher_inputs/txt/example_affairs.txt
unit_input_basis:
  committee: jurisdiction_text
  subcommittees:
    Subcommittee on Example Area A: jurisdiction_text
    Subcommittee on Example Area B: name_only
---

# Committee jurisdiction

[Eine Zeile mit dem möglichst originalgetreu übernommenen Jurisdiction-Text des Parent-Committees.]

## Subcommittee on Example Area A

[Eine Zeile mit dem möglichst originalgetreu übernommenen Jurisdiction-Text dieses Subcommittees.]

## Subcommittee on Example Area B

[Für `name_only` steht hier transparent, dass kein separater Jurisdiktionstext
vorliegt und nur der offizielle Subcommittee-Name zusammen mit der
Parent-Jurisdiktion als Evidenz verwendet wird. Diesen Abschnitt für jedes
Subcommittee wiederholen.]

## Zusätzliche Kommentare

[Nicht eindeutig zuordenbare Quellinformationen; dieser reservierte Abschnitt wird niemals als Mapping-Unit oder Prompt-Kontext verwendet.]
