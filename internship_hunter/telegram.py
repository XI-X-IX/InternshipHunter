from __future__ import annotations

import html
import logging
import time

import requests

from .models import Job

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"

SOURCE_NAMES = {
    "jobup": "jobup.ch",
    "jobs_ch": "jobs.ch",
    "linkedin": "LinkedIn",
    "adzuna": "Adzuna",
}


class Telegram:
    def __init__(self, token: str, chat_id: str):
        if not token or not chat_id:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID doivent être définis (voir README)."
            )
        if chat_id == token.split(":", 1)[0]:
            raise ValueError(
                "TELEGRAM_CHAT_ID est l'identifiant du bot lui-même (le début du token). "
                "Il faut TON identifiant : lance `python -m internship_hunter --get-chat-id` ou "
                "ouvre https://api.telegram.org/bot<TOKEN>/getUpdates (voir README)."
            )
        self.token = token
        self.chat_id = chat_id
        self.session = requests.Session()

    def _call(self, method: str, **params) -> dict:
        url = API.format(token=self.token, method=method)
        for attempt in range(5):
            resp = self.session.post(url, json=params, timeout=20)
            if resp.status_code == 429:  # limite de débit Telegram
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                log.warning("Telegram: trop de messages, pause de %ss", wait)
                time.sleep(wait + 1)
                continue
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method}: {data.get('description')}")
            return data["result"]
        raise RuntimeError(f"Telegram {method}: échec après plusieurs tentatives")

    def send(self, text: str, buttons: list[list[dict]] | None = None) -> None:
        params = dict(chat_id=self.chat_id, text=text, parse_mode="HTML",
                      disable_web_page_preview=True)
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        self._call("sendMessage", **params)
        time.sleep(1.1)  # reste sous ~1 message/seconde par chat

    def send_plain(self, text: str, reply_to: int | None = None) -> None:
        """Texte brut (réponses de Claude), découpé sous la limite de 4096 caractères."""
        for chunk in split_text(text):
            params = dict(chat_id=self.chat_id, text=chunk, disable_web_page_preview=True)
            if reply_to:
                params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
            self._call("sendMessage", **params)
            reply_to = None
            time.sleep(1.1)

    def send_copyable(self, text: str, reply_to: int | None = None) -> None:
        """Texte dans un bloc <pre> : Telegram affiche un bouton « Copier »."""
        for chunk in split_escaped(text):
            params = dict(chat_id=self.chat_id, text=f"<pre>{chunk}</pre>",
                          parse_mode="HTML", disable_web_page_preview=True)
            if reply_to:
                params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
            self._call("sendMessage", **params)
            reply_to = None
            time.sleep(1.1)

    def get_updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0,
                          allowed_updates=["callback_query"])

    def answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except RuntimeError:
            pass  # clic trop ancien (> quelques minutes) : Telegram refuse, sans gravité


def split_text(text: str, limit: int = 4000) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def split_escaped(text: str, limit: int = 4000) -> list[str]:
    """Découpe en morceaux dont la version échappée HTML tient sous la limite Telegram."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        for piece in split_text(line, limit=limit // 6) if len(line) > limit // 6 else [line]:
            esc = html.escape(piece)
            if len(current) + len(esc) > limit and current:
                chunks.append(current)
                current = ""
            current += esc
    if current:
        chunks.append(current)
    return chunks


def job_buttons(job: Job) -> list[list[dict]]:
    # callback_data est limité à 64 octets : "cv|jobs_ch:<uuid>" en fait 47
    return [[
        {"text": "🎯 Adapter mon CV", "callback_data": f"cv|{job.key}"[:64]},
        {"text": "✉️ Lettre", "callback_data": f"lt|{job.key}"[:64]},
    ]]


def format_job(job: Job, highlighted: bool = False) -> str:
    e = html.escape
    star = "⭐ " if highlighted else ""
    lines = [f"{star}<b>{e(job.title)}</b>"]
    if job.company:
        lines.append(f"🏢 {e(job.company)}")
    if job.location:
        lines.append(f"📍 {e(job.location)}")
    meta = SOURCE_NAMES.get(job.source, job.source)
    if job.published:
        meta = f"{job.published:%d.%m.%Y} · {meta}"
    lines.append(f"🗓 {e(meta)}")
    lines.append(f'🔗 <a href="{e(job.url, quote=True)}">Voir l\'offre</a>')
    return "\n".join(lines)


def get_chat_ids(token: str) -> list[tuple[str, str]]:
    """Retourne (chat_id, nom) des conversations ayant écrit récemment au bot."""
    resp = requests.get(API.format(token=token, method="getUpdates"), timeout=20).json()
    if not resp.get("ok"):
        raise RuntimeError(resp.get("description"))
    chats = {}
    for upd in resp["result"]:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
            chats[str(chat["id"])] = name
    return list(chats.items())
