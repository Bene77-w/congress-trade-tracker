"""
Tags-Modul fuer Congress Trade Tracker.

Tags sind Pipeline-interne Kontext-Anreicherungen: ein Tag schreibt zusaetzliche
Spalten an einen Trade, ohne ihn auszuschliessen. Aktuell implementiert:
    - CommitteeTag    (Filter 3 in der User-Sprache): Insider_Sectors,
                       Relevant_Sectors.
    - VoteTimingTag   (Filter 4 in der User-Sprache): Vote_Timing_Hit*.
"""
