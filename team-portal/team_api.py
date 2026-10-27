"""Team HTTP adapter. Domain validation and storage stay with team-manager."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "team-manager"))
from team_store import TeamStore
from team_adapter import saved_evidence
from team_activity import activity_payload, scan_activity


def payload(audience="shared", as_of=None):
    if audience not in {"shared", "private"}:
        raise ValueError("audience must be shared or private")
    day = date.fromisoformat(as_of) if as_of else date.today()
    store = TeamStore()
    result = store.portal_payload(audience=audience, as_of=day)
    result["evidence"] = saved_evidence(result["members"], audience=audience)
    result["activity"] = activity_payload(store, [m["member_key"] for m in result["members"]])
    if audience == "shared":
        for member in result["activity"]["members"].values():
            member["observations"] = [
                {key: item.get(key) for key in ("source", "kind", "title", "url", "observed_at")}
                for item in member["observations"]
            ]
            member["errors"] = []
            member["sources"] = {
                source: {"last_success_at": state.get("last_success_at")}
                for source, state in member["sources"].items()
            }
            member.pop("last_scan", None)
            member.pop("running_scan", None)
    return result


def _flatten_mutation(action: str, data: dict) -> dict:
    if action == "assignment-create":
        nested = data.get("data")
        if isinstance(nested, dict):
            merged = dict(nested)
            if "member_key" in data and "member_key" not in merged:
                merged["member_key"] = data["member_key"]
            return merged
        return data
    if action in {"assignment-set", "assignment-note", "note"}:
        patch = data.get("patch")
        if isinstance(patch, dict):
            merged = dict(patch)
            if "id" not in merged:
                merged["id"] = data.get("id") or data.get("assignment_id")
            if "revision" not in merged:
                merged["revision"] = data.get("revision")
            return merged
        return {
            "id": data.get("id") or data.get("assignment_id"),
            "revision": data.get("revision"),
            **{k: v for k, v in data.items() if k not in {"id", "assignment_id", "revision"}},
        }
    if action == "meeting-create":
        nested = data.get("data")
        if isinstance(nested, dict):
            merged = dict(nested)
            if "member_key" in data and "member_key" not in merged:
                merged["member_key"] = data["member_key"]
            return merged
        return data
    if action == "meeting-save":
        patch = data.get("patch")
        if isinstance(patch, dict):
            merged = dict(patch)
            if "id" not in merged:
                merged["id"] = data.get("id") or data.get("meeting_id")
            if "revision" not in merged:
                merged["revision"] = data.get("revision")
            return merged
        return {
            "id": data.get("id") or data.get("meeting_id"),
            "revision": data.get("revision"),
            **{k: v for k, v in data.items() if k not in {"id", "meeting_id", "revision"}},
        }
    if action == "member-set":
        out = dict(data)
        if "id" not in out and "member_key" in out:
            out["id"] = out.pop("member_key")
        return out
    if action == "member-add":
        out = dict(data)
        if "member_key" not in out and "id" in out:
            out["member_key"] = out.pop("id")
        return out
    return data


def mutate(action, data):
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    if action == "activity-scan":
        if set(data) != {"member_key"} or not isinstance(data["member_key"], str):
            raise ValueError("activity scan requires member_key only")
        try:
            return scan_activity(TeamStore(), data["member_key"])
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
    return TeamStore().portal_mutate(action, _flatten_mutation(action, data))
