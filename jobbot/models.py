from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime


def normalize(text: str) -> str:
    """Minuscules, sans accents, espaces compactés."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


# Mentions qui varient d'un site à l'autre pour la même offre : genre (H/F, H-F-X, m/f/d…)
# et taux d'activité (100%, 80-100 %)
_GENDER = re.compile(r"\(?\b[hfmwdx](\s*[/|-]\s*[hfmwdx]){1,2}\b\)?")
_RATE = re.compile(r"\b\d{1,3}(\s*(-|a|to)\s*\d{1,3})?\s*%")


def title_key(title: str) -> str:
    t = normalize(title)
    t = _GENDER.sub(" ", t)
    t = _RATE.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


@dataclass
class Job:
    source: str
    id: str
    title: str
    company: str
    location: str
    url: str
    published: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def fingerprint(self) -> str:
        """Identifie la même offre publiée sur plusieurs sites."""
        title = title_key(self.title)
        company = re.sub(r"[^a-z0-9]+", " ", normalize(self.company)).strip()
        return f"{title}|{company}"
