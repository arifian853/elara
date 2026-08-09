"""
scripts/elara_admin_skill.py — Hermes Skill Bridge for Elara Admin API.

Allows Hermes (Elara on Telegram) to query and update leads, check stats,
and handle natural language requests like "ada leads tuh?", "/leads", "/stats".

Usage:
  python scripts/elara_admin_skill.py leads
  python scripts/elara_admin_skill.py lead <lead_id>
  python scripts/elara_admin_skill.py status <lead_id> <new_status>
  python scripts/elara_admin_skill.py stats
"""

from __future__ import annotations

import os
import sys
import json
import urllib.request
import urllib.parse

# Ensure stdout handles UTF-8 emojis on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API_BASE_URL = os.environ.get("ELARA_API_URL", "http://localhost:8001")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "248a7eb38c2c5940797c24cb2f8cb3a194fcfd2199b34cf2")


def _make_request(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    headers = {
        "X-Admin-Token": ADMIN_TOKEN,
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload else None

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_leads() -> str:
    url = f"{API_BASE_URL}/admin/leads"
    try:
        leads = _make_request(url)
        if not leads:
            return "Nggak ada leads project request saat ini."

        lines = [f"📋 <b>Daftar Project Request Leads ({len(leads)} Total):</b>\n"]
        for i, l in enumerate(leads[:5], 1):
            status_icon = "🆕" if l['status'] == 'new' else "📞" if l['status'] == 'contacted' else "✅" if l['status'] == 'won' else "❌"
            lines.append(
                f"{i}. {status_icon} <b>[{l['service'] or 'General'}]</b> — {l['description'][:50]}...\n"
                f"   💰 {l['budget']} | ⏱️ {l['deadline']} | 📱 <code>{l['contact']}</code>\n"
                f"   ID: <code>{l['id']}</code> | Status: <b>{l['status'].upper()}</b>\n"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching leads: {e}"


def get_lead_detail(lead_id: str) -> str:
    url = f"{API_BASE_URL}/admin/leads/{lead_id}"
    try:
        lead = _make_request(url)
        return (
            f"📝 <b>Detail Lead:</b> <code>{lead['id']}</code>\n\n"
            f"<b>Layanan:</b> {lead['service']}\n"
            f"<b>Deskripsi:</b> {lead['description']}\n"
            f"<b>Budget:</b> {lead['budget']}\n"
            f"<b>Deadline:</b> {lead['deadline']}\n"
            f"<b>Kontak:</b> <code>{lead['contact']}</code>\n"
            f"<b>Status:</b> {lead['status'].upper()}\n"
            f"<b>Dibuat Pada:</b> {lead['created_at']}"
        )
    except Exception as e:
        return f"Error fetching lead detail: {e}"


def update_lead_status(lead_id: str, new_status: str) -> str:
    url = f"{API_BASE_URL}/admin/leads/{lead_id}"
    try:
        res = _make_request(url, method="PATCH", payload={"status": new_status})
        return f"✅ Status lead <code>{lead_id}</code> berhasil diubah menjadi <b>{new_status.upper()}</b>."
    except Exception as e:
        return f"Error updating lead status: {e}"


def get_stats() -> str:
    url = f"{API_BASE_URL}/admin/stats"
    try:
        stats = _make_request(url)
        return (
            f"📊 <b>Statistik Elara Public System:</b>\n\n"
            f"• 💼 <b>Leads Project Request:</b> {stats.get('leads', 0)}\n"
            f"• 📄 <b>Dokumen Knowledge Base:</b> {stats.get('documents', 0)}\n"
            f"• 🧩 <b>Total Chunks Embedding:</b> {stats.get('chunks', 0)}"
        )
    except Exception as e:
        return f"Error fetching stats: {e}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python elara_admin_skill.py <leads|lead|status|stats> [args...]")
        return

    cmd = sys.argv[1].lower()

    if cmd in ("leads", "/leads", "list"):
        print(get_leads())
    elif cmd in ("lead", "/lead") and len(sys.argv) > 2:
        print(get_lead_detail(sys.argv[2]))
    elif cmd in ("status", "/status") and len(sys.argv) > 3:
        print(update_lead_status(sys.argv[2], sys.argv[3]))
    elif cmd in ("stats", "/stats"):
        print(get_stats())
    else:
        print(get_leads())


if __name__ == "__main__":
    main()
