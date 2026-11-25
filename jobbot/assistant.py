"""Conseils CV et lettres de motivation personnalisés, générés par Claude."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import anthropic

from .config import ROOT, Config

log = logging.getLogger(__name__)

SYSTEM = """Tu es un conseiller carrière qui aide un·e étudiant·e à décrocher un stage en Suisse \
(Lausanne, Neuchâtel, Fribourg). On te donne son CV, un profil détaillé tiré de son portfolio \
(projets, chiffres, parcours) et une offre de stage.

Règles :
- Ne jamais inventer d'expérience, de diplôme ou de compétence qui n'est pas dans le CV. \
Si l'offre demande quelque chose d'absent, dis-le franchement et propose comment le compenser.
- Sois concret : cite les éléments réels du CV et du profil (projets, chiffres, expériences) et \
de l'annonce. Le profil sert de réservoir de preuves : propose d'ajouter au CV un projet du \
portfolio quand il colle mieux à l'offre que ceux déjà présents.
- Réponds en texte brut pour Telegram : pas de Markdown (*, #, **), utilise les emojis et \
les tirets indiqués. Sois concis."""

CV_TASK = """Analyse l'adéquation entre mon CV et cette offre, et dis-moi comment adapter mon CV.
Réponds en français, exactement dans ce format :

🎯 Correspondance : X/10 — une phrase qui justifie la note

✅ À mettre en avant
- 3 à 5 éléments de mon CV, du plus au moins pertinent pour cette offre

➕ Mots-clés de l'annonce à intégrer
- seulement ceux qui correspondent vraiment à mon profil, en disant où les placer

✏️ Accroche pour le haut du CV (dans la langue de l'annonce, 2-3 lignes)

🔧 Lignes à reformuler (au plus 3)
- Avant : ... → Après : ...

⚠️ Points faibles à anticiper en entretien (1 ou 2)

🤖 Passage ATS (estimation) : X/100
Note estimée de mon CV face au logiciel de tri des candidatures (ATS) de l'entreprise, \
calculée sur 3 critères, chacun noté :
- Mots-clés (sur 60) : part des compétences et outils exigés par l'annonce présents \
mot pour mot dans mon CV. Liste ceux qui manquent alors que je les ai (dans mon profil), \
et ceux qui manquent vraiment.
- Intitulés et structure (sur 20) : titres de sections standards (Expérience, Formation, \
Compétences), intitulé du poste visé proche de celui de l'annonce, dates lisibles.
- Lisibilité (sur 20) : un ATS lit le texte brut du PDF. Signale ce qui le gêne dans mon CV : \
mise en page en colonnes qui mélange l'ordre, lettres espacées (nom lu lettre par lettre), \
texte dans des images ou icônes, informations importantes seulement dans l'en-tête.
Termine par les 3 corrections qui feraient gagner le plus de points pour CETTE offre.
Précise que c'est une estimation : chaque ATS a ses propres règles."""

LETTER_TASK = """Rédige une lettre de motivation pour cette offre, à partir de mon CV et de mon profil.
- Dans la langue de l'annonce (français, allemand ou anglais).
- Format suisse : objet, formule d'appel, 3 ou 4 paragraphes, formule de politesse. 350 mots maximum.
- Accroche spécifique à l'entreprise (pas de phrase générique), 2 ou 3 exemples précis tirés de \
mon CV ou de mon profil (projets, chiffres) reliés aux missions de l'annonce, disponibilités si le \
CV les mentionne. Si mon profil indique un portfolio, mentionne-le pour le détail des projets.
- Mets entre [crochets] ce que je dois compléter moi-même (nom du contact, date de début…).
Donne uniquement la lettre, sans commentaire avant ou après."""

TASKS = {"cv": CV_TASK, "lt": LETTER_TASK}


class AssistantError(RuntimeError):
    """Erreur à afficher telle quelle à l'utilisateur."""


def _resolve(path: str) -> Path | None:
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    return p if p.is_file() else None


def find_cv(cfg: Config) -> Path | None:
    return _resolve(cfg.cv_path)


def cv_block(path: Path) -> dict:
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            "title": "Mon CV",
        }
    else:
        block = {"type": "text", "text": "Mon CV :\n\n" + path.read_text(encoding="utf-8")}
    return block


def profile_blocks(cfg: Config, cv: Path) -> list[dict]:
    """CV + profil détaillé. Identiques à chaque demande : mis en cache (fin du préfixe stable)."""
    blocks = [cv_block(cv)]
    profile = _resolve(cfg.profile_path) if cfg.profile_path else None
    if profile:
        blocks.append({"type": "text", "text": profile.read_text(encoding="utf-8")})
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def offer_text(job_title: str, company: str, location: str, description: str) -> str:
    return (
        f"Offre : {job_title}\nEntreprise : {company}\nLieu : {location}\n\n"
        f"Texte de l'annonce :\n{description or '(texte complet indisponible, base-toi sur le titre)'}"
    )


def manual_prompt(kind: str, job_title: str, company: str, location: str, description: str) -> str:
    """Mode copier-coller (sans clé API) : message à coller dans le projet Claude « Stages »,
    qui contient déjà le CV et les règles (voir claude_project_instructions.md)."""
    return offer_text(job_title, company, location, description) + "\n\n" + TASKS[kind]


def generate(cfg: Config, kind: str, job_title: str, company: str, location: str,
             description: str) -> str:
    if not cfg.anthropic_api_key:
        raise AssistantError(
            "Il manque la clé API Claude : ajoute le secret ANTHROPIC_API_KEY sur GitHub (voir README)."
        )
    cv = find_cv(cfg)
    if not cv:
        raise AssistantError(
            f"Je ne trouve pas ton CV ({cfg.cv_path}). Ajoute ton CV en PDF à la racine du repo "
            "sous le nom cv.pdf (voir README)."
        )

    offer = offer_text(job_title, company, location, description)
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    try:
        response = client.beta.messages.create(
            model=cfg.claude_model,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": profile_blocks(cfg, cv) + [
                    {"type": "text", "text": offer + "\n\n" + TASKS[kind]}
                ],
            }],
        )
    except anthropic.AuthenticationError:
        raise AssistantError("La clé API Claude (ANTHROPIC_API_KEY) est refusée : vérifie-la.")
    except anthropic.PermissionDeniedError:
        raise AssistantError("La clé API Claude n'a pas accès à ce modèle (ou crédit épuisé).")
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower():
            raise AssistantError("Plus de crédit sur ton compte Claude API (console.anthropic.com).")
        raise
    if response.stop_reason == "refusal":
        raise AssistantError("Claude n'a pas pu traiter cette offre.")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    log.info("Claude %s : %d tokens en entrée (%d depuis le cache), %d en sortie",
             kind, response.usage.input_tokens, response.usage.cache_read_input_tokens or 0,
             response.usage.output_tokens)
    return text
