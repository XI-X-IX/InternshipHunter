from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Iterable

import requests

from ..config import Config
from ..models import Job

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-CH,fr;q=0.9,de;q=0.7,en;q=0.6",
        }
    )
    return s


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET avec quelques tentatives en cas d'erreur réseau ou 429/5xx."""
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=25, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


Fetcher = Callable[[Config, requests.Session], Iterable[Job]]


def all_sources() -> dict[str, Fetcher]:
    from . import adzuna, jobcloud, linkedin

    return {
        "jobup": jobcloud.fetch_jobup,
        "jobs_ch": jobcloud.fetch_jobs_ch,
        "linkedin": linkedin.fetch,
        "adzuna": adzuna.fetch,
    }
