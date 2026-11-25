from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .config import Config
from .models import Job, normalize


def keyword_regex(keyword: str) -> re.Pattern:
    """Mot entier (« IA » ne matche pas « stagiaire »). Un * final = préfixe :
    « informati* » matche informatique, informaticien, Informatik."""
    kw = normalize(keyword)
    prefix = kw.endswith("*")
    kw = re.escape(kw.rstrip("*")).replace(r"\ ", r"[\s-]+")
    return re.compile(rf"(?<![a-z0-9]){kw}" + ("" if prefix else r"(?![a-z0-9])"))


class JobFilter:
    def __init__(self, cfg: Config):
        self.must = [re.compile(normalize(p), re.I) for p in cfg.title_must_match]
        self.exclude = [normalize(k) for k in cfg.exclude_keywords]
        self.highlight = [keyword_regex(k) for k in cfg.highlight_keywords]
        self.only_highlighted = cfg.only_highlighted and bool(self.highlight)
        self.max_age = timedelta(days=cfg.max_age_days) if cfg.max_age_days else None

    def accepts(self, job: Job) -> bool:
        title = normalize(job.title)
        if self.must and not any(r.search(title) for r in self.must):
            return False
        if any(re.search(rf"\b{re.escape(k)}\b", title) for k in self.exclude):
            return False
        if self.only_highlighted and not self.is_highlighted(job):
            return False
        if self.max_age and job.published:
            published = job.published
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - published > self.max_age:
                return False
        return True

    def is_highlighted(self, job: Job) -> bool:
        title = normalize(job.title)
        return any(r.search(title) for r in self.highlight)
