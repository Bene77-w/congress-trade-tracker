# Implementierungsplan: Policy Area → Yahoo Finance Sektor Mapping

Dieses Dokument dient als technische Referenz für die Verknüpfung von Gesetzgebungsdaten (Congress.gov) mit Marktdaten (Yahoo Finance). Es basiert auf der Logik des Moduls `vote_sector_mapping.py`.

## 1. Übersicht & Zielsetzung
Der Mapping-Mechanismus ist ein Kernbestandteil von **Filter 4 (Vote Timing)**. Er validiert, ob ein Trade thematisch zu einer Abstimmung im Kongress passt, indem er die CRS-klassifizierten Policy Areas auf die elf Standard-Sektoren von Yahoo Finance mappt.

---

## 2. Eindeutige Sektor-Zuordnungen
Die folgenden Mappings gelten als stabil. Eine Policy Area wird maximal drei Sektoren zugeordnet, um eine hohe Signalqualität zu gewährleisten.

| Policy Area (Congress.gov) | Yahoo Finance Sektoren | Fokus / Beispiele |
| :--- | :--- | :--- |
| **Agriculture and Food** | Consumer Defensive | Lebensmittelhersteller, Agrar-Rohstoffe |
| **Armed Forces and National Security** | Industrials, Technology | Verteidigung, Cybersecurity, Halbleiter |
| **Energy** | Energy, Utilities | Öl, Gas, Erneuerbare Energien, Netzbetreiber |
| **Environmental Protection** | Energy, Utilities, Basic Materials | Emissionsstandards, Chemie, Bergbau |
| **Finance and Financial Sector** | Financial Services | Banken, Versicherungen, Fintech |
| **Health** | Healthcare | Pharma, Biotech, Krankenversicherungen |
| **Housing and Community Development** | Real Estate, Financial Services | REITs, Homebuilder, Hypothekenbanken |
| **Public Lands and Natural Resources** | Energy, Basic Materials | Bohrrechte, Bergbau, Holzwirtschaft |
| **Science, Technology, Communications** | Technology, Communication Services | Software, KI, Telekommunikation, Medien |
| **Transportation and Public Works** | Industrials | Bau, Transport, Logistik, Infrastruktur |
| **Water Resources Development** | Utilities, Industrials | Wasserversorger, Infrastrukturbau |
| **Emergency Management** | Industrials | Notfall-Equipment, Wiederaufbau |

---

## 3. Unsichere oder Kontext-abhängige Bereiche
Diese Bereiche erfordern entweder eine manuelle Prüfung oder die Analyse durch einen spezialisierten KI-Agenten, da sie zu breit gefächert sind.

### A. Analyse via KI-Agent erforderlich(wird später in den Kongress Tracker implementiert)
* **Commerce:** Deckt FTC, Verbraucherschutz und Wettbewerbsrecht ab. Betrifft oft *Consumer Cyclical* oder *Technology*.
* **Crime and Law Enforcement:** Umfasst Waffengesetze (*Industrials*), Überwachung (*Technology*) und Privatgefängnisse.
* **Government Operations and Politics:** Relevant bei Bundesverträgen (IT/Cloud) oder Contracting-Reformen.

### B. Kein direkter Bezug zu Einzelaktien (Systemisch)
Die folgenden Bereiche beeinflussen eher den Gesamtmarkt oder staatliche Institutionen als spezifische Branchen:
* **Taxation / Economics and Public Finance:** Steuergesetze und Schuldenobergrenzen wirken marktweit.
* **Foreign Policy / International Affairs:** Sanktionen sind zu spezifisch für statisches Mapping.
* **Labor and Employment / Immigration:** Auswirkungen sind meist diffus über alle Sektoren verteilt.

### C. Wirtschaftlich irrelevant für Sektor-Filter
* **Gesellschaft:** Education, Families, Social Welfare, Civil Rights and Liberties.
* **Kultur & Sport:** Arts, Culture, Religion, Sports and Recreation.
* **Interna:** Congress, Law, Native Americans.

---

## 4. Technische Implementierungs-Logik

### Validierung
Jeder Sektor muss gegen die `VALID_SECTORS` Liste geprüft werden:
`Technology`, `Healthcare`, `Financial Services`, `Energy`, `Industrials`, `Consumer Cyclical`, `Consumer Defensive`, `Real Estate`, `Utilities`, `Basic Materials`, `Communication Services`.

### Hilfsfunktionen
1.  **`get_relevant_sectors(policy_area)`**: Gibt die Liste der Sektoren zurück.
2.  **`is_sector_relevant(policy_area, trade_sector)`**: Führt den finalen Abgleich für den Filter durch.
3.  **`get_mapping_coverage()`**: Berechnet die Abdeckung (derzeit ca. 37,5% fest gemappt).

---

## 5. Wartung & Updates
Das Mapping sollte regelmäßig überprüft werden, insbesondere wenn Yahoo Finance neue Sektoren einführt oder das CRS (Congressional Research Service) die Policy Areas anpasst.
