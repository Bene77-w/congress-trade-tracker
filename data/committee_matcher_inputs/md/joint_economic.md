---
schema_version: 1
congress: 119
chamber: joint
committee: Joint Economic Committee
committee_type: joint
source_url: null
retrieved_at: null
source_metadata_status: pending
source_files:
  - data/committee_matcher_inputs/txt/joint_economic.txt
input_basis: summarized_mandate
---

# Committee jurisdiction


- Employment Act of 1946 (§ 5); 20 Mitglieder (10 Senat, 10 House), Vorsitz rotiert kammerweise je Kongress
- Keine Unterausschüsse mit eigener Jurisdiktion (anders als die 38 Standing Committees)
- Aufgaben: Prüfung des Economic Report of the President + eigener Jahresbericht; Untersuchung gesamtwirtschaftlicher Entwicklungen; Anhörungen (Fed, CEA, BLS/BEA/CBO, externe Ökonomen), traditionell rund um zentrale Datenveröffentlichungen; Studien von Mehrheits- und Minderheitsstab (oft gegensätzlich); Empfehlungen an die Fachausschüsse ohne Umsetzungskompetenz


## Zusätzliche Kommentare

Präambel aus der Quelldatei entfernt: `input text für joint committee on economic:`.

Vom Mapping-Prompt vollständig ausgeschlossen, da dies bereits vorweggenommene Sektor-, Industry-, Gewichtungs- und Reasoner-Empfehlungen sind: Kernaussage „Beratendes Analyse-Gremium ohne Gesetzgebungszuständigkeit — kann keine Gesetzentwürfe berichten, keine Mittel bewilligen, keine Behörde beaufsichtigen. Folge fürs Mapping: breiter Makro-Themenradius, minimale regulatorische Durchgriffsmacht auf einzelne Industries.“

Vom Mapping-Prompt vollständig ausgeschlossen: Themenradius → Sektoren: Geldpolitik, Zinsen, Inflation → Financials, Real Estate, Homebuilders, Utilities; Arbeitsmarkt, Löhne, Produktivität → breiter Markt, Staffing, Consumer Discretionary; Fiskalpolitik, Steuern, Schulden → breiter Markt, Treasuries; Handel, Zölle, Lieferketten → Industrials, Semiconductors, Retail, Agriculture; Energiepreise, Klimaökonomie → Energy, Utilities; Gesundheitskosten, Demografie → Healthcare, Insurance; Technologie-/KI-Ökonomie, Wettbewerb → Information Technology, Communication Services; Wohnkosten, Regionalökonomie → Real Estate, Materials.

Vom Mapping-Prompt vollständig ausgeschlossen: Gewichtungshinweise: „Niedriges Basisgewicht. Sektorspezifische Informationsvorteile eines Mitglieds stammen praktisch immer aus dessen Standing-Committee-Sitzen, nicht aus dem JEC.“ „Signal ist makro, nicht mikro — eher Index-ETFs und zinssensitive Titel als Einzelwerte.“ „Doppelzählung vermeiden: JEC-Mitglieder sitzen meist zusätzlich in Finance/Banking/Ways and Means.“ „Vorsitz beachten: prägt die Hearing-Agenda stark → dynamischer Themen-Pull statt statischer Sektorliste.“ „Stabil vs. volatil: Mandat/Struktur seit 1946 konstant, Themenagenda wechselt je Kongress.“ Kurzformel für den Reasoner: „Beratendes Makro-Gremium ohne Gesetzgebungsmacht; Themenradius gesamtwirtschaftlich; Sektorzuordnung breit und schwach gewichtet."
