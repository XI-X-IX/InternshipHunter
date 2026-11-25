"""Offres publiques LinkedIn (endpoint « guest », sans connexion)."""
from __future__ import annotations

import logging
import re
import time
from typing import Iterator

import requests
from bs4 import BeautifulSoup

from ..config import Config
from ..models import Job
from . import get, parse_date

log = logging.getLogger(__name__)

URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 10  # LinkedIn renvoie 10 offres par page
MAX_PAGES = 4


def parse_cards(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.base-card, div.job-search-card"):
        urn = card.get("data-entity-urn", "")
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        href = link.get("href", "") if link else ""
        m = re.search(r"(\d{6,})", urn) or re.search(r"-(\d{6,})(?:\?|$)", href)
        title_el = card.select_one(".base-search-card__title, h3")
        if not m or not title_el:
            continue
        job_id = m.group(1)
        company_el = card.select_one(".base-search-card__subtitle, h4")
        loc_el = card.select_one(".job-search-card__location")
        time_el = card.select_one("time")
        jobs.append(
            Job(
                source="linkedin",
                id=job_id,
                title=title_el.get_text(" ", strip=True),
                company=company_el.get_text(" ", strip=True) if company_el else "",
                location=loc_el.get_text(" ", strip=True) if loc_el else "",
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                published=parse_date(time_el.get("datetime")) if time_el else None,
            )
        )
    return jobs


def fetch(cfg: Config, session: requests.Session) -> Iterator[Job]:
    distance_miles = max(1, round(cfg.radius_km / 1.609))
    for location in cfg.locations:
        for term in cfg.search_terms:
            for page in range(MAX_PAGES):
                params = {
                    "keywords": term,
                    "location": f"{location}, Switzerland",
                    "distance": distance_miles,
                    "f_TPR": f"r{cfg.max_age_days * 86400}" if cfg.max_age_days else None,
                    "start": page * PAGE_SIZE,
                }
                resp = get(session, URL, params={k: v for k, v in params.items() if v})
                jobs = parse_cards(resp.text)
                yield from jobs
                if len(jobs) < PAGE_SIZE:
                    break
                time.sleep(2)
            time.sleep(2)
