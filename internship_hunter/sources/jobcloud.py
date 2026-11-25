"""jobup.ch et jobs.ch (même plateforme JobCloud, API JSON publique du site)."""
from __future__ import annotations

import logging
import time
from typing import Iterator

import requests

from ..config import Config
from ..models import Job
from . import get, parse_date

log = logging.getLogger(__name__)

SITES = {
    "jobup": ("https://www.jobup.ch", "/fr/emplois/detail/{id}/"),
    "jobs_ch": ("https://www.jobs.ch", "/fr/offres-emplois/detail/{id}/"),
}
ROWS = 20  # maximum accepté par l'API (sinon erreur 422)
MAX_PAGES = 5


def parse_documents(source: str, data: dict) -> list[Job]:
    base, detail = SITES[source]
    jobs = []
    for doc in data.get("documents", []) or []:
        job_id = str(doc.get("job_id") or doc.get("id") or "")
        title = (doc.get("title") or "").strip()
        if not job_id or not title:
            continue
        links = doc.get("_links") or {}
        url = None
        for lang in ("detail_fr", "detail_en", "detail_de"):
            href = (links.get(lang) or {}).get("href")
            if href:
                url = href
                break
        if not url:
            url = base + detail.format(id=job_id)
        company = doc.get("company_name") or (doc.get("company") or {}).get("name") or ""
        place = doc.get("place") or doc.get("location") or ""
        if isinstance(place, list):
            place = ", ".join(str(p) for p in place)
        jobs.append(
            Job(
                source=source,
                id=job_id,
                title=title,
                company=str(company).strip(),
                location=str(place).strip(),
                url=url,
                published=parse_date(doc.get("publication_date") or doc.get("initial_publication_date")),
            )
        )
    return jobs


def _fetch(source: str, cfg: Config, session: requests.Session) -> Iterator[Job]:
    base, _ = SITES[source]
    for location in cfg.locations:
        for term in cfg.search_terms:
            for page in range(1, MAX_PAGES + 1):
                params = {"query": term, "location": location, "page": page, "rows": ROWS}
                resp = get(
                    session,
                    f"{base}/api/v1/public/search",
                    params=params,
                    headers={"Accept": "application/json"},
                )
                data = resp.json()
                jobs = parse_documents(source, data)
                yield from jobs
                num_pages = data.get("num_pages") or 1
                if page >= num_pages or len(jobs) < ROWS:
                    break
                time.sleep(1)
            time.sleep(1)


def fetch_jobup(cfg: Config, session: requests.Session) -> Iterator[Job]:
    return _fetch("jobup", cfg, session)


def fetch_jobs_ch(cfg: Config, session: requests.Session) -> Iterator[Job]:
    return _fetch("jobs_ch", cfg, session)
