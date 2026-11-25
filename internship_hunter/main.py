from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from .config import Config, load_config
from .filters import JobFilter
from .models import Job
from .sources import all_sources, make_session
from .assistant import TASKS, AssistantError, generate, manual_prompt
from .details import fetch_description
from .storage import Storage
from .telegram import Telegram, format_job, get_chat_ids, job_buttons

log = logging.getLogger("internship_hunter")


def collect(cfg: Config) -> tuple[list[Job], list[str], list[str]]:
    """Interroge toutes les sources activées. Retourne (offres, sources OK, sources en échec)."""
    session = make_session()
    jobs: list[Job] = []
    ok, failed = [], []
    for name, fetch in all_sources().items():
        if not cfg.sources.get(name, False):
            continue
        if name == "adzuna" and not (cfg.adzuna_app_id and cfg.adzuna_app_key):
            log.info("adzuna   ignoré (pas de clés API)")
            continue
        before = len(jobs)
        try:
            for job in fetch(cfg, session):
                jobs.append(job)
            ok.append(name)
            log.info("%-8s %d offres récupérées", name, len(jobs) - before)
        except Exception as exc:  # une source en panne ne doit pas bloquer les autres
            failed.append(name)
            log.warning("%-8s ÉCHEC après %d offres: %s", name, len(jobs) - before, exc)
    return jobs, ok, failed


def select_new(jobs: list[Job], flt: JobFilter, storage: Storage | None) -> list[Job]:
    unique: dict[str, Job] = {}
    for job in jobs:
        if job.fingerprint in unique or not flt.accepts(job):
            continue
        if storage and storage.is_seen(job):
            continue
        unique[job.fingerprint] = job
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def sort_key(j: Job):
        p = j.published
        if p is None:
            return oldest
        return p if p.tzinfo else p.replace(tzinfo=timezone.utc)

    return sorted(unique.values(), key=sort_key, reverse=True)


REQUEST_TITLES = {"cv": "🎯 Conseils CV pour", "lt": "✉️ Lettre de motivation pour"}


def handle_requests(cfg: Config, storage: Storage, tg: Telegram) -> int:
    """Traite les clics sur « Adapter mon CV » / « Lettre » depuis le dernier passage."""
    offset = int(storage.get_meta("tg_offset", "0"))
    try:
        updates = tg.get_updates(offset)
    except Exception as exc:
        log.warning("Impossible de lire les clics Telegram: %s", exc)
        return 0
    session = make_session()
    handled = 0
    for upd in updates:
        storage.set_meta("tg_offset", str(upd["update_id"] + 1))  # ne jamais retraiter un clic
        cq = upd.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(cfg.telegram_chat_id):
            continue  # clic venant d'une autre conversation : ignoré
        kind, _, key = (cq.get("data") or "").partition("|")
        if kind not in TASKS:
            continue
        msg_id = msg.get("message_id")
        tg.answer_callback(cq["id"], "⏳ C'est noté, je prépare ça…")
        if storage.request_done(key, kind):
            tg.send_plain("C'est déjà fait : regarde la réponse plus haut ⬆️", reply_to=msg_id)
            continue
        job = storage.get_job(key)
        if not job:
            tg.send_plain("⚠️ Je ne retrouve plus cette offre dans ma mémoire.", reply_to=msg_id)
            continue
        source, _, job_id = key.partition(":")
        log.info("Demande %s pour « %s »", kind, job["title"])
        try:
            try:
                description = fetch_description(source, job_id, job["url"], session)
            except Exception as exc:
                log.warning("Texte de l'offre indisponible (%s), analyse sur le titre seul", exc)
                description = ""
            if not cfg.anthropic_api_key:
                # Mode copier-coller : pas de clé API, on prépare le message pour claude.ai
                prompt = manual_prompt(kind, job["title"], job["company"], job["location"], description)
                tg.send_plain(
                    f"📋 {REQUEST_TITLES[kind]} : {job['title']} — {job['company']}\n\n"
                    "Copie le message ci-dessous (bouton « Copier » du bloc) et colle-le dans une "
                    "nouvelle conversation de ton projet Claude « Stages ».",
                    reply_to=msg_id,
                )
                tg.send_copyable(prompt)
                handled += 1
                continue
            text = generate(cfg, kind, job["title"], job["company"], job["location"], description)
            header = f"{REQUEST_TITLES[kind]} : {job['title']} — {job['company']}"
            tg.send_plain(f"{header}\n\n{text}", reply_to=msg_id)
            storage.mark_request_done(key, kind)
            handled += 1
        except AssistantError as exc:
            tg.send_plain(f"⚠️ {exc}", reply_to=msg_id)
        except Exception:
            log.exception("Échec de la demande %s", kind)
            tg.send_plain("⚠️ Une erreur est survenue, réessaie en recliquant sur le bouton.",
                          reply_to=msg_id)
    return handled


def run_once(cfg: Config, *, dry_run: bool = False, seed: bool = False) -> int:
    flt = JobFilter(cfg)
    storage = Storage(cfg.database_path)
    try:
        tg = None
        if not (dry_run or seed):
            tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
            # Les demandes CV / lettre d'abord : ce sont elles que tu attends
            handled = handle_requests(cfg, storage, tg)
            if handled:
                log.info("%d demande(s) CV / lettre traitée(s)", handled)

        jobs, ok, failed = collect(cfg)
        new = select_new(jobs, flt, storage)
        log.info("%d offres brutes, %d nouvelles offres de stage", len(jobs), len(new))

        if dry_run:
            for job in new:
                star = "⭐" if flt.is_highlighted(job) else " "
                print(f"{star} [{job.source}] {job.title} — {job.company} ({job.location})\n    {job.url}")
            return 0

        if seed:
            for job in new:
                storage.mark_seen(job)
            log.info("%d offres marquées comme déjà vues (aucune notification)", len(new))
            return 0

        if failed and not ok:
            tg.send("⚠️ InternshipHunter : toutes les sources ont échoué (" + ", ".join(failed) + "). Vérifie les logs.")
            return 1

        to_send = new[: cfg.max_notifications_per_run]
        for job in to_send:
            tg.send(format_job(job, flt.is_highlighted(job)), buttons=job_buttons(job))
            storage.mark_seen(job)  # marqué seulement une fois envoyé
        remaining = len(new) - len(to_send)
        if remaining > 0:
            tg.send(f"➕ Encore {remaining} offre(s) en attente, elles arriveront au prochain passage (dans ~30 min).")
        log.info("%d notifications envoyées (%d offres en mémoire)", len(to_send), storage.count())
        return 0
    finally:
        storage.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="internship_hunter",
        description="Cherche des stages autour de Lausanne, Neuchâtel et Fribourg et notifie sur Telegram.",
    )
    parser.add_argument("--config", help="chemin du fichier de configuration YAML")
    parser.add_argument("--once", action="store_true", help="une seule recherche puis quitter (cron / GitHub Actions)")
    parser.add_argument("--dry-run", action="store_true", help="affiche les offres trouvées sans rien envoyer")
    parser.add_argument("--seed", action="store_true", help="enregistre les offres actuelles comme vues, sans notifier")
    parser.add_argument("--test", action="store_true", help="envoie un message de test sur Telegram")
    parser.add_argument("--get-chat-id", action="store_true", help="affiche le chat_id des conversations avec le bot")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    cfg = load_config(args.config)

    if args.get_chat_id:
        if not cfg.telegram_token:
            log.error("Définis d'abord TELEGRAM_BOT_TOKEN, puis envoie un message à ton bot.")
            return 1
        chats = get_chat_ids(cfg.telegram_token)
        if not chats:
            print("Aucun message reçu : envoie « /start » à ton bot sur Telegram puis relance.")
        for chat_id, name in chats:
            print(f"TELEGRAM_CHAT_ID={chat_id}   ({name})")
        return 0

    if args.test:
        Telegram(cfg.telegram_token, cfg.telegram_chat_id).send(
            "✅ InternshipHunter est bien connecté ! Tu recevras ici les nouvelles offres de stage autour de "
            + ", ".join(cfg.locations) + "."
        )
        log.info("Message de test envoyé")
        return 0

    if args.once or args.dry_run or args.seed:
        return run_once(cfg, dry_run=args.dry_run, seed=args.seed)

    log.info("Mode continu : recherche toutes les %d minutes (Ctrl+C pour arrêter)", cfg.interval_minutes)
    while True:
        try:
            run_once(cfg)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Erreur pendant la recherche")
        time.sleep(cfg.interval_minutes * 60)


if __name__ == "__main__":
    sys.exit(main())
