"""Récupère le texte complet d'une offre (pour l'analyse CV / lettre)."""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .sources import get
from .sources.jobcloud import SITES

MAX_CHARS = 15000


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def fetch_description(source: str, job_id: str, url: str, session: requests.Session) -> str:
    if source in SITES:
        base, _ = SITES[source]
        data = get(session, f"{base}/api/v1/public/search/job/{job_id}").json()
        parts = [data.get("template_lead_text"), data.get("template_text")]
        text = "\n\n".join(html_to_text(p) for p in parts if p)
        if not text.strip():
            text = html_to_text(data.get("template", ""))
    elif source == "linkedin":
        html = get(session, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}").text
        soup = BeautifulSoup(html, "html.parser")
        block = soup.select_one(".show-more-less-html__markup, .description__text")
        text = block.get_text("\n", strip=True) if block else html_to_text(html)
    else:
        text = html_to_text(get(session, url).text)
    return text[:MAX_CHARS]
