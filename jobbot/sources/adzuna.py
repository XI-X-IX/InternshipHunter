"""API officielle Adzuna (gratuite, nécessite ADZUNA_APP_ID / ADZUNA_APP_KEY)."""
from __future__ import annotations

import logging
import time
from typing import Iterator

import requests

from ..config import Config
from ..models import Job
from . import get, parse_date

log = logging.getLogger(__name__)

URL = "https://api.adzuna.com/v1/api/jobs/ch/search/{page}"


def parse_results(data: dict) -> list[Job]:
    jobs = []
    for r in data.get("results", []) or []:
        if not r.get("id") or not r.get("title"):
            continue
        jobs.append(
            Job(
                source="adzuna",
                id=str(r["id"]),
                title=r["title"].strip(),
                company=((r.get("company") or {}).get("display_name") or "").strip(),
                location=((r.get("location") or {}).get("display_name") or "").strip(),
                url=r.get("redirect_url") or "",
                published=parse_date(r.get("created")),
            )
        )
    return jobs


def fetch(cfg: Config, session: requests.Session) -> Iterator[Job]:
    if not (cfg.adzuna_app_id and cfg.adzuna_app_key):
        log.info("Adzuna ignoré (pas de clés API)")
        return
    for location in cfg.locations:
        for term in cfg.search_terms:
            params = {
                "app_id": cfg.adzuna_app_id,
                "app_key": cfg.adzuna_app_key,
                "what": term,
                "where": location,
                "distance": cfg.radius_km,
                "results_per_page": 50,
                "sort_by": "date",
            }
            if cfg.max_age_days:
                params["max_days_old"] = cfg.max_age_days
            resp = get(session, URL.format(page=1), params=params)
            yield from parse_results(resp.json())
            time.sleep(1)
