"""yfinance-Adapter des Committee Matchers V2 (Plan 2.4).

Einziger Ort im Paket, der Netzwerk beruehrt. Der Adapter hat genau eine
Aufgabe: aus der Live-Taxonomie die vier Identitaetsfelder und die Beschreibung
holen - und alles andere **am Rand verwerfen**.

Boersengroessen (Market Cap, Market Weight, Firmen- und Mitarbeiterzahlen)
liefert yfinance mit. Sie werden hier weggeschnitten und nie gespeichert,
ausgewertet oder an ein Modell gesendet: eine Zuordnung soll die fachliche
Naehe von Jurisdiktion und Industry belegen, nicht die Groesse eines Marktes.
``INDUSTRY_FIELDS`` ist die abschliessende Allowlist; ``_only_allowed`` ist der
Filter, durch den jeder Datensatz muss.

Contract-Spike (Plan P4, gegen die gepinnte Version geprueft):

- ``yfinance.const.SECTOR_INDUSTY_MAPPING`` liefert die Sector-Seeds (11);
- ``Sector(key).industries`` liefert einen DataFrame mit Index ``key`` und den
  Spalten ``name``, ``symbol``, ``market weight``;
- ``Industry(key).overview`` liefert unter anderem ``description``,
  ``market_cap``, ``market_weight``, ``companies_count``, ``employee_count``.

Der Import dieses Moduls zieht yfinance bewusst nicht nach - erst der erste
Abruf importiert. Damit bleibt der providerfreie Kern importierbar, ohne dass
eine Netzwerkbibliothek geladen wird.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator

from .errors import (
    SILENT_REPORTER, ErrorCollector, MatcherError, Stage, TerminalReporter,
)
from .settings_view import OfflineSettings

#: Abschliessende Feldliste eines Snapshoteintrags.
INDUSTRY_FIELDS = ("industry_id", "industry_name", "sector_id", "sector_name", "description")

#: Felder, die yfinance mitliefert und die den Adapter nie verlassen duerfen.
DISCARDED_FIELDS = (
    "market_cap",
    "market weight",
    "market_weight",
    "companies_count",
    "employee_count",
    "industries_count",
    "message_board_id",
    "symbol",
)


class TaxonomyFetchError(MatcherError):
    """Nicht behebbarer Abruffehler; traegt den Befund."""


class EmptySectorResponse(RuntimeError):
    """Leere Sectorantwort; retrybar, da fast immer eine Drosselung."""


class EmptyOverviewResponse(RuntimeError):
    """Leere Industry-Overview; retrybar, da fast immer eine Drosselung."""


@dataclass(frozen=True)
class SectorSeed:
    """Ein Sector-Seed aus den gepinnten yfinance-Konstanten."""

    sector_id: str
    sector_name: str


@dataclass(frozen=True)
class IndustryRow:
    """Eine Industry, bereits auf die erlaubten Felder reduziert."""

    industry_id: str
    industry_name: str
    sector_id: str
    sector_name: str
    description: str = ""

    def as_record(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in INDUSTRY_FIELDS}

    @classmethod
    def from_payload(cls, payload: dict) -> "IndustryRow":
        """Baut eine Zeile aus einem Rohdatensatz - alles Fremde faellt weg."""
        return cls(**{**{"description": ""}, **_only_allowed(payload)})


def _only_allowed(payload: dict) -> dict[str, str]:
    """Schneidet jeden Datensatz auf die Allowlist zurueck."""
    return {key: str(value) for key, value in payload.items() if key in INDUSTRY_FIELDS}


class YFinanceClient:
    """Duenne Huelle um yfinance mit Pause und begrenzten Retries.

    Die Pause ist kein Hoeflichkeitsdetail: ohne sie beantwortet Yahoo Abrufe
    ueber alle elf Sektoren mit Rate-Limits, und ein halb abgerufener Snapshot
    ist wertlos. Retries sind bewusst endlich - ein dauerhaft gestoerter Abruf
    soll blockieren statt endlos zu laufen.
    """

    def __init__(
        self,
        settings: OfflineSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        reporter: TerminalReporter = SILENT_REPORTER,
    ) -> None:
        self._pause = settings.yfinance_request_pause_s
        self._retries = settings.yfinance_max_retries
        #: Schrittweite des run-lokalen Checkpoints (Verbraucher: taxonomy_snapshot).
        self.checkpoint_every = settings.yfinance_checkpoint_every
        self._sleep = sleep
        #: Ein erfolgreicher Einzelabruf bleibt still; nur Wiederholungen sind
        #: fuer den Nutzer relevant (Statusplan S3).
        self._reporter = reporter

    # -- Infrastruktur -----------------------------------------------------
    def _call(self, what: str, func: Callable[[], object]) -> object:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            if attempt or self._pause:
                self._sleep(self._pause)
            try:
                return func()
            except Exception as exc:  # yfinance wirft breit; Ursache steht im Befund
                last = exc
                if attempt < self._retries:
                    self._reporter.warn(
                        Stage.TAXONOMY, object=what,
                        retry=f"{attempt + 1}/{self._retries}",
                        reason=exc.__class__.__name__)
        raise TaxonomyFetchError(
            [
                _finding(
                    "TAX_FETCH_FAILED", what,
                    f"Abruf nach {self._retries + 1} Versuchen fehlgeschlagen "
                    f"({last.__class__.__name__}: {last}).",
                    "Netzwerk oder yfinance-Verfuegbarkeit pruefen; danach "
                    "'snapshot resume --run-id ...' fortsetzen.",
                )
            ],
            f"Taxonomieabruf fehlgeschlagen: {what}",
        )

    # -- Abrufe ------------------------------------------------------------
    def version(self) -> str:
        import yfinance

        return str(yfinance.__version__)

    def sector_seeds(self) -> tuple[SectorSeed, ...]:
        """Gepinnte Sector-Seeds; die Industries entdeckt erst der Livelauf."""
        from yfinance.const import (
            SECTOR_INDUSTY_MAPPING,
            SECTOR_INDUSTY_MAPPING_LC,
        )

        # Die Konstante fuehrt Anzeigenamen, ``Sector(...)`` erwartet den Key.
        # Beide Listen stammen aus derselben gepinnten Version; weicht ihre
        # Laenge ab, ist der Contract verletzt.
        names = sorted(SECTOR_INDUSTY_MAPPING)
        keys = sorted(SECTOR_INDUSTY_MAPPING_LC)
        if len(names) != len(keys):
            raise TaxonomyFetchError(
                [
                    _finding(
                        "TAX_CONTRACT_CHANGED", "sector_seeds",
                        f"{len(names)} Sektornamen stehen {len(keys)} Sektorkeys gegenueber.",
                        "yfinance-Version pruefen.",
                    )
                ],
                "yfinance-Contract abweichend",
            )
        return tuple(
            SectorSeed(sector_id=key, sector_name=name)
            for key, name in zip(keys, names)
        )

    def industries(self, seed: SectorSeed) -> tuple[IndustryRow, ...]:
        import yfinance

        def fetch() -> object:
            frame = yfinance.Sector(seed.sector_id).industries
            # Ein leerer Sector ist in der Praxis eine gedrosselte Antwort und
            # keine Tatsache. Als Ausnahme faellt er in die Retrylogik, statt
            # den ganzen Abruf mit einem unvollstaendigen Ergebnis zu beenden.
            if not any(True for _ in _iter_frame(frame)):
                raise EmptySectorResponse(seed.sector_id)
            return frame

        frame = self._call(f"sector {seed.sector_id}", fetch)
        rows: list[IndustryRow] = []
        for industry_id, record in _iter_frame(frame):
            # Rohsatz aus dem DataFrame plus Sektorherkunft; 'symbol' und
            # 'market weight' fallen in from_payload weg.
            payload = dict(record)
            payload.update(
                industry_id=str(industry_id),
                industry_name=str(record.get("name", "")).strip(),
                sector_id=seed.sector_id,
                sector_name=seed.sector_name,
            )
            rows.append(IndustryRow.from_payload(payload))
        return tuple(rows)

    def description(self, industry_id: str) -> str:
        import yfinance

        def fetch() -> object:
            overview = yfinance.Industry(industry_id).overview
            # Wie beim leeren Sector: eine leere oder formfremde Antwort ist in
            # der Praxis eine Drosselung. Sie faellt deshalb in die Retrylogik,
            # statt den Lauf sofort mit TAX_DESCRIPTION_MISSING zu beenden.
            if not isinstance(overview, dict):
                raise EmptyOverviewResponse(industry_id)
            text = str(overview.get("description", "")).strip()
            if not text:
                raise EmptyOverviewResponse(industry_id)
            return text

        return str(self._call(f"industry {industry_id}", fetch))


def _iter_frame(frame: object) -> Iterator[tuple[str, dict]]:
    """Iteriert das Industries-Ergebnis unabhaengig von der Rueckgabeform."""
    rows = getattr(frame, "iterrows", None)
    if callable(rows):
        for key, row in frame.iterrows():  # type: ignore[union-attr]
            yield str(key), dict(row)
        return
    if isinstance(frame, dict):
        for key, row in frame.items():
            yield str(key), dict(row)
        return
    raise TaxonomyFetchError(
        [
            _finding(
                "TAX_CONTRACT_CHANGED", "Sector.industries",
                f"Unerwarteter Rueckgabetyp {type(frame).__name__}.",
                "yfinance-Version pruefen; der Adapter ist auf die gepinnte "
                "Version zugeschnitten.",
            )
        ],
        "yfinance-Contract abweichend",
    )


def _finding(code: str, obj: str, message: str, hint: str):
    collector = ErrorCollector()
    return collector.blocker(code, Stage.TAXONOMY, obj, message, hint)


def fetch_taxonomy(
    collector: ErrorCollector,
    client: YFinanceClient,
    *,
    known: dict[str, IndustryRow] | None = None,
    on_progress: Callable[[IndustryRow], None] | None = None,
    reporter: TerminalReporter = SILENT_REPORTER,
) -> tuple[IndustryRow, ...]:
    """Holt die vollstaendige Taxonomie; ``known`` ueberspringt Bekanntes.

    ``known`` traegt die bereits abgerufenen Industries eines Checkpoints. Der
    Fortschritt wird ueber ``on_progress`` gemeldet, damit der Aufrufer nach
    jeder Industry checkpointen kann, ohne dass der Adapter Dateien kennt.
    """
    known = known or {}
    seeds = client.sector_seeds()
    if not seeds:
        collector.blocker(
            "TAX_NO_SEEDS", Stage.TAXONOMY, "sector_seeds",
            "Die gepinnten yfinance-Konstanten liefern keine Sector-Seeds.",
            "yfinance-Version pruefen.",
        )
        return ()

    result: list[IndustryRow] = []
    for seed in seeds:
        reporter.start(Stage.TAXONOMY, sector=seed.sector_id)
        rows = client.industries(seed)
        if not rows:
            collector.blocker(
                "TAX_SECTOR_EMPTY", Stage.TAXONOMY, seed.sector_id,
                "Sector liefert keine Industries.",
                "Abruf wiederholen; ein leerer Sector blockiert den Snapshot.",
            )
            continue
        for row in rows:
            # Identitaet stammt immer aus dem frischen Sectorabruf; aus dem
            # Checkpoint kommt ausschliesslich die teure Beschreibung. Sonst
            # entstuende beim Resume ein Mischsnapshot, in dem eine zwischen
            # Abbruch und Fortsetzung umbenannte oder umsortierte Industry mit
            # ihrer alten Identitaet weiterlaeuft - und die Driftpruefung
            # genau diese Aenderung nicht mehr sehen wuerde.
            cached = known.get(row.industry_id)
            description = cached.description if cached is not None else ""
            if not description:
                description = client.description(row.industry_id)
            if not description:
                # Der Livecient laesst es dazu praktisch nicht kommen - eine
                # leere Overview ist dort retrybar. Der Befund bleibt als Netz
                # fuer jede andere Clientimplementierung stehen.
                collector.blocker(
                    "TAX_DESCRIPTION_MISSING", Stage.TAXONOMY, row.industry_id,
                    "Industry ohne Beschreibung.",
                    "Abruf wiederholen; ohne Beschreibung fehlt dem Mapping die "
                    "fachliche Grundlage.",
                )
                continue
            complete = IndustryRow(
                industry_id=row.industry_id,
                industry_name=row.industry_name,
                sector_id=row.sector_id,
                sector_name=row.sector_name,
                description=description,
            )
            result.append(complete)
            if on_progress is not None:
                # Auch uebernommene Beschreibungen werden gemeldet, damit der
                # Checkpoint die aktuelle Identitaet traegt.
                on_progress(complete)
    return tuple(result)
