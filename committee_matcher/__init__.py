"""Committee Matcher V2 - providerfreier Kern.

Dieses Paket ist der einzige Laufzeitcode des neu implementierten Committee
Matchers. Verbindliche Grundlage sind
``organisation/committee_matcher_v2_implementierungsplan.md`` und
``organisation/committee_matcher_planung_entscheidungen_codex.md``.

Ownership-Karte (Zielgroesse 12-18 kohaerente Laufzeitmodule; jede
Zustaendigkeit existiert genau einmal):

===========================  ===========================================  =====
Modul                        Zustaendigkeit                               Phase
===========================  ===========================================  =====
``status``                   Workflow-/Unit-/Coverage-Zustaende, Exit-     P2
                             Codes, Aufzaehlungen des Fachvertrags
``errors``                   Fehlercodes, Befundsammlung, strukturierte    P2
                             Persistenzform, Terminalbericht
``paths``                    Pfadwache: erlaubte Lese-/Schreibwurzeln,     P2
                             Traversalblock, Schutz der Produktionspfade
``settings_view``            typisierte, validierte Sicht auf              P2
                             ``COMMITTEE_MATCHER_*`` inkl. Providergate
``models``                   Pydantic-Datenvertraege (Input, Roster,       P2
                             Taxonomie, Mapping)
``cli``                      CLI-Familien, Exit-Code-Mapping, Bericht      P2
``input_corpus``             Corpusmanifest, strikter MD-Parser,           P3
                             Fremdanweisungsgate
``units``                    Unitbildung, ``unit-id-v1``-Kanonisierung,    P3
                             fachliche Inputhashes, 225-ID-Sollmenge
``roster_coverage``          einzige Roster-Coverage-Implementierung       P3
``taxonomy_source``          yfinance-Adapterrand (verwirft Market-Cap-    P4
                             und Weight-Felder)
``taxonomy_snapshot``        Snapshot, Checkpoint/Resume, Katalog,         P4
                             ``taxonomy_content_hash``, Driftgate
``prompts``                  Systemprompts laden, Version/Hash pruefen,    P5
                             Fremdtext als Datenblock abgrenzen
``provider``                 Routing-Preflight, providerneutrales          P5
                             Structured-Output-Interface, FakeProvider
                             und OpenRouter-Adapter
``mapping_engine``           Batching, Context-Gate, formale Validierung,  P6
                             gezielter Repair
``workflow``                 Runzustand, Cache/Resume, Kostenkontrolle     P7
``artifacts``                Long-CSV, Coverage, Manifest, Review-XLSX     P8
``evaluation``               Gold-Set-, Stabilitaets- und Holdoutmetriken  P9
===========================  ===========================================  =====

Invarianten des providerfreien Kerns:

- Der Import dieses Pakets zieht weder ``openrouter`` noch ``yfinance`` oder
  eine externe HTTP-Bibliothek nach; einzige Ausnahme ist das stdlib-``urllib``
  in ``provider.py``, das den Katalog erst beim Aufruf anfasst. Netzzugriff
  entsteht in P4 (yfinance) und im Routing-Preflight (oeffentlicher
  OpenRouter-Katalog, kein Key).
- Providerabhaengige Settings duerfen unset sein, blockieren dann aber jeden
  Providerpfad (``settings_view.MatcherSettings.require_provider``).
- Geschrieben wird ausschliesslich unter ``data/committee_matcher_v2/``; der
  Inputroot und die matcherbezogenen Produktionspfade sind schreibgeschuetzt
  (``paths.assert_writable``).

Die eigenstaendigen P0-Werkzeuge ``scripts/committee_matcher_migrate_inputs.py``
und ``scripts/committee_matcher_baseline.py`` bleiben bewusst unabhaengig von
diesem Paket; ihre Corpus- und Manifestlogik wandert in P3 nach
``input_corpus`` und die Skripte werden dort zu duennen CLI-Huellen.
"""
from __future__ import annotations

#: Version des providerfreien Geruests; wandert in Run- und Snapshotmanifeste.
SCAFFOLD_VERSION = "scaffold-v1"

__all__ = ["SCAFFOLD_VERSION"]
