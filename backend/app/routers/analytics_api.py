from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from .. import convo_runtime as cr
from ..auth import current_account
from ..db import Account, Conversation, Persona, Replica, Video, get_session
from ..leads import analytics_block as leads_analytics
from ..models_features import ConversationMetric, VideoBatch

router = APIRouter()


def _pct(vals: list[float], p: float):
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(len(v) * p))], 1)


@router.get("/analytics")
def analytics(days: int = 30, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    days = min(max(days, 1), 365)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = lambda d: cr.utc(d).date().isoformat()  # noqa: E731

    convs = [c for c in s.exec(select(Conversation).where(Conversation.account_id == acc.id)).all() if cr.utc(c.started_at) >= since]
    per_day: dict[str, dict] = defaultdict(lambda: {"conversations": 0, "minutes": 0.0})
    for c in convs:
        d = per_day[day(c.started_at)]
        d["conversations"] += 1
        d["minutes"] += (c.seconds_used or 0) / 60
    # dense series so charts have no gaps
    series = []
    for i in range(days - 1, -1, -1):
        k = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        v = per_day.get(k, {"conversations": 0, "minutes": 0.0})
        series.append({"date": k, "conversations": v["conversations"], "minutes": round(v["minutes"], 2)})

    metrics = [m for m in s.exec(select(ConversationMetric).where(ConversationMetric.account_id == acc.id)).all()
               if cr.utc(m.created_at) >= since]
    lat = [m.avg_first_audio_ms for m in metrics if m.avg_first_audio_ms]
    p95 = [m.p95_first_audio_ms for m in metrics if m.p95_first_audio_ms]

    personas = {p.id: p.name for p in s.exec(select(Persona).where(Persona.account_id == acc.id)).all()}
    top: dict[str, dict] = defaultdict(lambda: {"conversations": 0, "minutes": 0.0})
    for c in convs:
        t = top[c.persona_id]
        t["conversations"] += 1
        t["minutes"] += (c.seconds_used or 0) / 60
    top_personas = sorted(({"persona_id": k, "name": personas.get(k, "(deleted)"), "conversations": v["conversations"],
                            "minutes": round(v["minutes"], 2)} for k, v in top.items()),
                          key=lambda x: (-x["conversations"], -x["minutes"]))[:10]

    vids = [v for v in s.exec(select(Video).where(Video.account_id == acc.id)).all() if cr.utc(v.created_at) >= since]
    by_status: dict[str, int] = defaultdict(int)
    vday: dict[str, int] = defaultdict(int)
    for v in vids:
        by_status[v.status] += 1
        vday[day(v.created_at)] += 1
    replicas = s.exec(select(Replica).where(Replica.account_id == acc.id)).all()
    return {
        "range_days": days,
        "totals": {"conversations": len(convs), "minutes": round(sum(c.seconds_used or 0 for c in convs) / 60, 2),
                   "user_turns": sum(m.user_turns for m in metrics), "agent_turns": sum(m.agent_turns for m in metrics),
                   "tool_calls": sum(m.tool_calls for m in metrics), "interruptions": sum(m.interruptions for m in metrics)},
        "conversations_per_day": series,
        "first_audio_latency_ms": {"avg": round(sum(lat) / len(lat), 1) if lat else None, "p50": _pct(lat, 0.5),
                                   "p95": _pct(p95, 0.95), "samples": len(lat)},
        "top_personas": top_personas,
        "videos": {"total": len(vids), "by_status": dict(by_status),
                   "per_day": [{"date": k, "videos": v} for k, v in sorted(vday.items())]},
        "replicas": {"total": len(replicas), "ready": sum(1 for r in replicas if r.status == "ready")},
        "credits_seconds": acc.credits_seconds,
        **leads_analytics(s, acc.id, convs, personas, since),  # leads + objective completion rates (templates-leads module)
    }
