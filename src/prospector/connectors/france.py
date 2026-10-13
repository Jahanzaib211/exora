"""French company register (INPI / official recherche-entreprises API): public registry discovery.

Searches the French government's own company search (data.gouv.fr, Etalab licence) by name or activity and
returns the registered trade name, SIREN, registered office and the officers listed on the register.

Compliance: registry filings companies make by law, published openly by the State. Records marked
non-diffusable (statut_diffusion = N) are skipped entirely, and only natural-person officers are kept -
their professional role (gerant, president...) with the register page as the receipt. Birth dates some
register entries carry are never read or stored; the campaign only ever sees a name and a business title.

Politeness: plain keyless GETs paced by the shared PoliteClient (0.2s between hits on this host).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec

API = "https://recherche-entreprises.api.gouv.fr/search"
PAGE = "https://annuaire-entreprises.data.gouv.fr/entreprise/"
INTERVAL = 0.2
PER_PAGE = 25  # the API's hard maximum

#: etat_administratif (INSEE) -> registry_status used everywhere else in the app
ETAT = {"A": "ACTIVE", "C": "CLOSED", "F": "CLOSED", "I": "INACTIVE"}


class FranceRegistryConnector(Connector):
    key = "france-registry"
    name = "French company register"
    description = (
        "French companies by name or activity from the official register: SIREN, registered office and "
        "listed officers. Keyless."
    )
    licence = "Etalab licence (recherche-entreprises.api.gouv.fr)"
    params = [
        ParamSpec("query", "Search (name or activity)", "text", True, help='e.g. "cabinet d\'avocat lyon"'),
        ParamSpec("limit", "Max records", "int", False, default=25),
    ]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        query = str(params.get("query") or "").strip()
        if not query:
            raise ValueError("a search query is required")
        limit = max(1, min(int(params.get("limit") or 25), 500))
        page = 1
        fetched = 0
        while fetched < limit:
            size = min(PER_PAGE, limit - fetched)
            try:
                body = self.http.get(
                    API, params={"q": query, "per_page": size, "page": page}, interval=INTERVAL, timeout=40
                ).json()
            except ValueError:
                return  # the API answers non-JSON on overload; the run ends quietly
            except Exception:
                return  # host unreachable or rate-limited: nothing is claimed, the run ends quietly
            if body.get("erreur"):
                raise ValueError(f"recherche-entreprises: {body['erreur']}")
            results = body.get("results") or []
            if not results:
                return
            for item in results:
                rec = self._record(item, query)
                if rec is not None:
                    fetched += 1
                    yield rec
                    if fetched >= limit:
                        return
            if page * PER_PAGE >= int(body.get("total_results") or 0):
                return  # every result page has been read; never loop on a repeated last page
            page += 1

    def _record(self, item: dict[str, Any], query: str) -> CompanyRecord | None:
        siren = str(item.get("siren") or "")
        if not siren or item.get("statut_diffusion") == "N":
            return None  # non-diffusable records: the register asks relying parties not to process them
        name = (item.get("nom_complet") or item.get("nom_raison_sociale") or "").strip()
        if not name:
            return None
        siege = item.get("siege") or {}
        founded = None
        created = str(item.get("date_creation") or "")
        if created[:4].isdigit():
            founded = int(created[:4])
        url = PAGE + siren
        people = self._officers(item, url)
        return CompanyRecord(
            source="france-registry",
            url=url,
            name=name,
            country="FR",
            city=(siege.get("libelle_commune") or "").title() or None,
            address=(siege.get("adresse") or "").strip() or None,
            company_number=siren,
            registry_status=ETAT.get(str(item.get("etat_administratif") or "")),
            founded=founded,
            confidence=0.95,
            people=people,
            evidence=[api_evidence(f"{API}?q={query}", item)],
        )

    def _officers(self, item: dict[str, Any], url: str) -> list[PersonRecord]:
        """Natural-person officers only, with their register role; corporate officers are skipped."""
        out: list[PersonRecord] = []
        for d in item.get("dirigeants") or []:
            if str(d.get("type_dirigeant") or "").lower() != "personne physique":
                continue  # a company listed as an officer is not a contactable person
            last = str(d.get("nom") or "").strip()
            first = str(d.get("prenoms") or "").strip()
            if not last or not first:
                continue
            role = str(d.get("qualite") or "").strip() or "Dirigeant"
            out.append(
                PersonRecord(
                    name=f"{first} {last}".title(),
                    title=role.capitalize(),
                    url=url,
                    excerpt=f"Official register entry: {role} of "
                    + str(item.get("nom_complet") or item.get("nom_raison_sociale") or ""),
                )
            )
        return out
