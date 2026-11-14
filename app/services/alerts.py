"""Email digests of newly found jobs that match your profile.

Uses plain SMTP, so any mailbox works. For Gmail, create an App Password
(Google Account → Security → 2-Step Verification → App passwords) and use
smtp.gmail.com, port 587.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from sqlmodel import Session, col, select

from ..config import config
from ..models import Job
from . import settings_store

log = logging.getLogger(__name__)


class AlertError(RuntimeError):
    pass


def _candidates(session: Session, min_score: float) -> list[Job]:
    return list(session.exec(
        select(Job).where(
            col(Job.alerted).is_(False), col(Job.closed_at).is_(None), Job.status != "hidden",
            col(Job.score).is_not(None), col(Job.score) >= min_score,
        ).order_by(col(Job.score).desc())
    ).all())


def baseline(session: Session) -> int:
    """Mark every current job as already alerted, so turning alerts on doesn't
    email the whole backlog."""
    jobs = session.exec(select(Job).where(col(Job.alerted).is_(False))).all()
    for j in jobs:
        j.alerted = True
        session.add(j)
    session.commit()
    return len(jobs)


def _app_url() -> str:
    host = "127.0.0.1" if config.host in ("0.0.0.0", "") else config.host
    return f"http://{host}:{config.port}"


def compose(jobs: list[Job], total: int) -> EmailMessage:
    top = jobs[0]
    msg = EmailMessage()
    more = f" (+{total - 1} more)" if total > 1 else ""
    msg["Subject"] = f"JobPilot: {total} new match{'es' if total != 1 else ''} — {top.title} at {top.company or 'n/a'}{more}"
    lines = [f"{total} new job{'s' if total != 1 else ''} match your profile.", ""]
    rows = []
    for j in jobs:
        where = " · ".join(x for x in (j.company, j.location, "Remote" if j.remote else "") if x)
        lines += [f"[{round(j.score or 0)}] {j.title} — {where}", f"    {j.url or _app_url()}", ""]
        rows.append(
            f"<tr><td style='padding:8px 10px;font-weight:700;color:{'#137a4b' if (j.score or 0) >= 70 else '#9a6200'}'>{round(j.score or 0)}</td>"
            f"<td style='padding:8px 10px'><a href='{escape(j.url or _app_url(), quote=True)}' style='color:#2952cc;font-weight:600;text-decoration:none'>{escape(j.title)}</a>"
            f"<div style='color:#5b6272;font-size:13px'>{escape(where)}</div></td></tr>")
    if total > len(jobs):
        lines.append(f"…and {total - len(jobs)} more in JobPilot.")
    lines += ["", f"Open JobPilot: {_app_url()}/#/jobs"]
    msg.set_content("\n".join(lines))
    msg.add_alternative(
        "<div style='font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:620px'>"
        f"<h2 style='margin:0 0 8px'>{total} new match{'es' if total != 1 else ''}</h2>"
        "<p style='color:#5b6272;margin:0 0 12px'>Newly found jobs that score well against your profile.</p>"
        f"<table style='border-collapse:collapse;width:100%'>{''.join(rows)}</table>"
        + (f"<p style='color:#5b6272'>…and {total - len(jobs)} more.</p>" if total > len(jobs) else "")
        + f"<p><a href='{_app_url()}/#/jobs' style='color:#2952cc'>Open JobPilot →</a></p></div>",
        subtype="html",
    )
    return msg


def send(cfg: dict, msg: EmailMessage) -> None:
    smtp = cfg.get("smtp") or {}
    to = (cfg.get("email_to") or "").strip()
    if not smtp.get("host") or not to:
        raise AlertError("Set the SMTP server and the address to send alerts to.")
    msg["To"] = to
    msg["From"] = smtp.get("from") or smtp.get("username") or to
    port = int(smtp.get("port") or 587)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(smtp["host"], port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(smtp["host"], port, timeout=30)
        with server:
            if port != 465 and smtp.get("use_tls", True):
                server.starttls(context=ssl.create_default_context())
            if smtp.get("username"):
                server.login(smtp["username"], smtp.get("password", ""))
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise AlertError("Email login failed. For Gmail, use an App Password, not your normal password.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise AlertError(f"Could not send email: {exc}") from exc


def notify_new_matches(session: Session) -> dict:
    """Email a digest of unalerted matches, if alerts are on. Safe to call often."""
    cfg = settings_store.get(session, "alerts")
    if not cfg.get("enabled"):
        return {"sent": False, "reason": "disabled"}
    jobs = _candidates(session, float(cfg.get("min_score") or 0))
    if not jobs:
        return {"sent": False, "reason": "no new matches"}
    limit = max(1, int(cfg.get("max_items") or 15))
    try:
        send(cfg, compose(jobs[:limit], len(jobs)))
    except AlertError as exc:
        log.warning("Alert email failed: %s", exc)
        return {"sent": False, "reason": str(exc), "matches": len(jobs)}
    for j in jobs:
        j.alerted = True
        session.add(j)
    session.commit()
    return {"sent": True, "matches": len(jobs)}


def send_test(session: Session) -> None:
    cfg = settings_store.get(session, "alerts")
    jobs = list(session.exec(select(Job).where(col(Job.score).is_not(None)).order_by(col(Job.score).desc()).limit(3)).all())
    if not jobs:
        jobs = [Job(source="manual", external_id="demo", title="Example role", company="Example Co", score=88, url=_app_url())]
    msg = compose(jobs, len(jobs))
    msg.replace_header("Subject", "JobPilot test alert — " + msg["Subject"].split(": ", 1)[1])
    send(cfg, msg)
