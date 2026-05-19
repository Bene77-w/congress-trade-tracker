"""
Gates-Modul fuer Congress Trade Tracker.

Gates sind Pipeline-interne Hard-Ausschluss-Mechanismen: ein Gate entscheidet
binaer, ob ein Trade ueberhaupt weiter bewertet wird. Aktuell implementiert:
    - AssetClassGate (Filter 0 in der User-Sprache): schliesst Nicht-Aktien aus.
"""
