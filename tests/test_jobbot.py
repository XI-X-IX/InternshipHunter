import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from jobbot import main as jm
from jobbot.config import load_config
from jobbot.filters import JobFilter
from jobbot.models import Job
from jobbot.sources import adzuna, jobcloud, linkedin
from jobbot.telegram import format_job

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_telegram_updates():
    with mock.patch.object(jm.Telegram, "get_updates", return_value=[]):
        yield


@pytest.fixture
def cfg(tmp_path):
    c = load_config(str(Path(__file__).parent.parent / "config.example.yaml"))
    c.database = str(tmp_path / "test.db")
    c.max_age_days = 0  # fixtures à dates fixes
    c.only_highlighted = False  # les tests génériques ne dépendent pas du domaine de l'exemple
    c.telegram_token, c.telegram_chat_id = "tok", "42"
    return c


def test_parse_jobup():
    jobs = jobcloud.parse_documents("jobup", json.loads((FIX / "jobup.json").read_text()))
    assert len(jobs) == 3
    assert jobs[0].url == "https://www.jobup.ch/fr/emplois/detail/a1b2c3/"
    assert jobs[0].company == "Nestlé SA"
    assert jobs[1].url == "https://www.jobup.ch/fr/emplois/detail/d4e5f6/"  # URL reconstruite
    assert jobs[0].published.year == 2026


def test_parse_linkedin():
    jobs = linkedin.parse_cards((FIX / "linkedin.html").read_text())
    assert [j.id for j in jobs] == ["4051234567", "4059999999"]
    assert jobs[0].title == "Data Science Intern"
    assert jobs[0].company == "EPFL"
    assert jobs[0].location.startswith("Lausanne")
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4051234567/"


def test_parse_adzuna():
    jobs = adzuna.parse_results(json.loads((FIX / "adzuna.json").read_text()))
    assert jobs[0].company == "UBS" and jobs[0].id == "5012345678"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Stagiaire en marketing digital", True),
        ("Stage en finance - 6 mois", True),
        ("Data Science Intern", True),
        ("Internship Software Engineering", True),
        ("Praktikant/in Finanzen", True),
        ("International Sales Manager", False),
        ("Médecin interne", False),
        ("Backstage technician", False),
        ("Apprenti(e) employé(e) de commerce - Stage", False),  # exclu
        ("Ingénieur logiciel senior", False),
    ],
)
def test_filter(cfg, title, expected):
    job = Job("x", "1", title, "", "", "u")
    assert JobFilter(cfg).accepts(job) is expected


def test_filter_age(cfg):
    cfg.max_age_days = 7
    old = Job("x", "1", "Stage", "", "", "u", datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert not JobFilter(cfg).accepts(old)


def test_format_escapes_html():
    msg = format_job(Job("jobup", "1", "Stage R&D <AI>", "A & B", "Lausanne", "https://x?a=1&b=2"), True)
    assert "R&amp;D &lt;AI&gt;" in msg and msg.startswith("⭐")
    assert 'href="https://x?a=1&amp;b=2"' in msg


def test_run_once_dedup_and_cap(cfg):
    jobs = [
        Job("jobup", "1", "Stagiaire RH", "Acme", "Lausanne", "u1"),
        Job("jobs_ch", "9", "Stagiaire RH", "ACME", "Lausanne", "u9"),  # doublon inter-sites
        Job("linkedin", "2", "Data Intern", "EPFL", "Lausanne", "u2"),
        Job("linkedin", "3", "Sales Manager", "X", "Neuchâtel", "u3"),  # pas un stage
        Job("adzuna", "4", "Praktikum IT", "Y", "Fribourg", "u4"),
    ]
    cfg.max_notifications_per_run = 2
    sent = []
    with mock.patch.object(jm, "collect", return_value=(jobs, ["jobup"], [])), \
         mock.patch.object(jm.Telegram, "send", lambda self, t, buttons=None: sent.append(t)):
        jm.run_once(cfg)
        assert len(sent) == 3 and "Encore 1 offre" in sent[-1]
        sent.clear()
        jm.run_once(cfg)  # la 3e offre arrive au passage suivant
        assert len(sent) == 1
        sent.clear()
        jm.run_once(cfg)
        assert sent == []


def test_all_sources_failed_alerts(cfg):
    sent = []
    with mock.patch.object(jm, "collect", return_value=([], [], ["jobup", "linkedin"])), \
         mock.patch.object(jm.Telegram, "send", lambda self, t, buttons=None: sent.append(t)):
        assert jm.run_once(cfg) == 1
    assert "toutes les sources ont échoué" in sent[0]


def test_jobcloud_pagination(cfg):
    cfg.locations, cfg.search_terms = ["Lausanne"], ["stage"]
    page = {"num_pages": 1, "documents": [{"job_id": "1", "title": "Stage"}]}
    resp = mock.Mock(json=lambda: page)
    with mock.patch.object(jobcloud, "get", return_value=resp) as g, mock.patch("time.sleep"):
        jobs = list(jobcloud.fetch_jobup(cfg, None))
    assert len(jobs) == 1 and g.call_count == 1
    assert g.call_args.kwargs["params"]["location"] == "Lausanne"


def test_skipped_adzuna_does_not_count_as_ok(cfg):
    cfg.sources = {"jobup": True, "adzuna": True}
    cfg.adzuna_app_id = cfg.adzuna_app_key = ""

    def boom(c, s):
        raise RuntimeError("down")

    with mock.patch.object(jm, "all_sources", return_value={"jobup": boom, "adzuna": boom}):
        jobs, ok, failed = jm.collect(cfg)
    assert ok == [] and failed == ["jobup"]


def test_rejects_bot_own_id_as_chat_id():
    from jobbot.telegram import Telegram
    with pytest.raises(ValueError, match="bot lui-même"):
        Telegram("123456789:AAAA", "123456789")


def test_only_highlighted(cfg):
    cfg.highlight_keywords, cfg.only_highlighted = ["informatique", "software"], True
    flt = JobFilter(cfg)
    assert flt.accepts(Job("x", "1", "Stage Software Engineer", "", "", "u"))
    assert not flt.accepts(Job("x", "2", "Stagiaire RH", "Vaudoise", "", "u"))


def test_excludes_mpc(cfg):
    assert not JobFilter(cfg).accepts(Job("x", "1", "Stagiaire MPC (3+1) - RH", "", "", "u"))


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Stage Data & IA Ingénieur (H/F)", True),
        ("FPGA Engineer Intern", False),
        ("Internship - Reasoning and agentic capabilities of LLMs", True),
        ("Stagiaire en analyse de données IT", True),
        ("Stagiaire informaticien CFC", True),
        ("Praktikum Softwareentwicklung", True),  # offres en allemand acceptées dans la config d'exemple
        ("Software Engineer Intern (m/w/d)", True),  # offres en allemand acceptées dans la config d'exemple
        ("Stage développeuse full-stack", True),
        ("Stagiaire RH gestion des processus", False),
        ("Stagiaire en marketing digital", False),
        ("Stagiaire universitaire - Projets stratégiques RH", False),
        ("Stagiaire formation institutionnelle non-clinique", False),
    ],
)
def test_example_domain_filter(title, expected):
    c = load_config(str(Path(__file__).parent.parent / "config.example.yaml"))
    c.max_age_days = 0
    assert JobFilter(c).accepts(Job("x", "1", title, "", "", "u")) is expected


# --- Demandes CV / lettre via les boutons Telegram ---------------------------

def _click(update_id, data, chat_id="42", message_id=7):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "data": data,
        "message": {"message_id": message_id, "chat": {"id": int(chat_id)}}}}


def test_buttons_fit_telegram_limit():
    from jobbot.telegram import job_buttons
    job = Job("jobs_ch", "85b1adb1-5414-4a0e-918b-8e82e60a884d", "Stage", "", "", "u")
    for b in job_buttons(job)[0]:
        assert len(b["callback_data"].encode()) <= 64
        assert b["callback_data"].endswith(job.key)


def test_handle_requests(cfg):
    from jobbot.storage import Storage
    cfg.anthropic_api_key = "sk-test"
    st = Storage(cfg.database_path)
    st.mark_seen(Job("jobup", "abc", "Stage Data", "Assura", "Lausanne", "https://x"))
    updates = [
        _click(10, "cv|jobup:abc"),
        _click(11, "cv|jobup:abc"),          # double clic
        _click(12, "lt|jobup:abc", chat_id="999"),  # autre conversation : ignoré
        _click(13, "lt|jobup:inconnu"),
    ]
    tg = jm.Telegram("tok", "42")
    sent = []
    with mock.patch.object(tg, "get_updates", return_value=updates), \
         mock.patch.object(tg, "answer_callback"), \
         mock.patch.object(tg, "send_plain", lambda t, reply_to=None: sent.append((t, reply_to))), \
         mock.patch.object(jm, "fetch_description", return_value="Missions : Python, SQL"), \
         mock.patch.object(jm, "generate", return_value="🎯 Correspondance : 8/10") as gen:
        assert jm.handle_requests(cfg, st, tg) == 1
    assert gen.call_count == 1
    assert gen.call_args.args[1:] == ("cv", "Stage Data", "Assura", "Lausanne", "Missions : Python, SQL")
    assert sent[0][0].startswith("🎯 Conseils CV pour : Stage Data — Assura")
    assert sent[0][1] == 7
    assert "déjà fait" in sent[1][0]
    assert "ne retrouve plus" in sent[2][0]
    assert st.get_meta("tg_offset") == "14"


def test_missing_key_or_cv_gives_clear_message(cfg, tmp_path):
    from jobbot.assistant import AssistantError, generate
    cfg.anthropic_api_key = ""
    with pytest.raises(AssistantError, match="ANTHROPIC_API_KEY"):
        generate(cfg, "cv", "Stage", "X", "Y", "desc")
    cfg.anthropic_api_key, cfg.cv_path = "sk-test", str(tmp_path / "absent.pdf")
    with pytest.raises(AssistantError, match="cv.pdf"):
        generate(cfg, "cv", "Stage", "X", "Y", "desc")


def test_generate_sends_cv_pdf_and_offer(cfg, tmp_path):
    from jobbot import assistant
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    profile = tmp_path / "profil.md"
    profile.write_text("Projet Shift Left : pipeline DevSecOps")
    cfg.anthropic_api_key, cfg.cv_path, cfg.profile_path = "sk-test", str(pdf), str(profile)
    fake = mock.Mock()
    fake.beta.messages.create.return_value = mock.Mock(
        stop_reason="end_turn",
        content=[mock.Mock(type="text", text="  Lettre  ")],
        usage=mock.Mock(input_tokens=1, cache_read_input_tokens=0, output_tokens=1),
    )
    with mock.patch.object(assistant.anthropic, "Anthropic", return_value=fake):
        assert assistant.generate(cfg, "lt", "Stage IA", "SQLI", "Lausanne", "Annonce") == "Lettre"
    kwargs = fake.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    doc, profile, task = kwargs["messages"][0]["content"]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert "Shift Left" in profile["text"] and profile["cache_control"]
    assert "cache_control" not in doc
    assert "SQLI" in task["text"] and "lettre de motivation" in task["text"]


def test_split_text():
    from jobbot.telegram import split_text
    parts = split_text(("ligne\n" * 2000), limit=4000)
    assert all(len(p) <= 4000 for p in parts) and "".join(parts).count("ligne") == 2000


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Stagiaire en cybersécurité SOC", True),
        ("Internship - Network Security Engineer", True),
        ("Praktikum Informationssicherheit", True),  # offres en allemand acceptées dans la config d'exemple
        ("Stagiaire administrateur systèmes Linux", True),
        ("Stage Sécurité au travail et santé", True),  # faux positif accepté (rare)
        ("Stagiaire comptabilité", False),
    ],
)
def test_security_keywords(title, expected):
    c = load_config(str(Path(__file__).parent.parent / "config.example.yaml"))
    c.max_age_days = 0
    assert JobFilter(c).accepts(Job("x", "1", title, "", "", "u")) is expected


def test_copy_paste_mode_without_api_key(cfg):
    from jobbot.storage import Storage
    cfg.anthropic_api_key = ""
    st = Storage(cfg.database_path)
    st.mark_seen(Job("linkedin", "123", "Stage Data & IA", "SQLI", "Lausanne", "https://x"))
    tg = jm.Telegram("tok", "42")
    plain, copy = [], []
    with mock.patch.object(tg, "get_updates", return_value=[_click(5, "lt|linkedin:123")]), \
         mock.patch.object(tg, "answer_callback"), \
         mock.patch.object(tg, "send_plain", lambda t, reply_to=None: plain.append(t)), \
         mock.patch.object(tg, "send_copyable", lambda t, reply_to=None: copy.append(t)), \
         mock.patch.object(jm, "fetch_description", return_value="Missions : LLM, Python"), \
         mock.patch.object(jm, "generate") as gen:
        assert jm.handle_requests(cfg, st, tg) == 1
    gen.assert_not_called()
    assert "projet Claude « Stages »" in plain[0]
    assert "Stage Data & IA" in copy[0] and "Missions : LLM, Python" in copy[0]
    assert "lettre de motivation" in copy[0]
    assert not st.request_done("linkedin:123", "lt")  # on peut recliquer


def test_send_copyable_escapes_and_splits():
    from jobbot.telegram import Telegram
    tg = Telegram("tok", "42")
    calls = []
    with mock.patch.object(tg, "_call", lambda m, **p: calls.append(p)), mock.patch("time.sleep"):
        tg.send_copyable("R&D <IA>\n" * 600)
    assert len(calls) > 1
    assert all(c["text"].startswith("<pre>") and len(c["text"]) <= 4096 for c in calls)
    assert "R&amp;D &lt;IA&gt;" in calls[0]["text"]


def test_cv_task_includes_ats_and_copy_prompt_carries_it():
    from jobbot.assistant import manual_prompt
    prompt = manual_prompt("cv", "Stage DevOps", "X", "Lausanne", "Docker, Kubernetes")
    assert "Passage ATS (estimation)" in prompt and "/100" in prompt
    assert "ATS" not in manual_prompt("lt", "Stage DevOps", "X", "Lausanne", "Docker")


@pytest.mark.parametrize(
    "a,b",
    [
        ("Stagiaire Analytics Engineer", "Stagiaire Analytics Engineer H-F-X"),
        ("Stage Data & IA Ingénieur (H/F)", "Stage Data & IA Ingénieur"),
        ("Data Intern (m/f/d)", "Data Intern"),
        ("Stagiaire IT 100%", "Stagiaire IT 80 - 100 %"),
    ],
)
def test_fingerprint_ignores_gender_and_rate(a, b):
    assert Job("x", "1", a, "Richemont", "", "u").fingerprint == Job("y", "2", b, "Richemont", "", "u").fingerprint


def test_fingerprint_keeps_real_differences():
    assert Job("x", "1", "Stagiaire Data", "A", "", "u").fingerprint != Job("x", "2", "Stagiaire IT", "A", "", "u").fingerprint
