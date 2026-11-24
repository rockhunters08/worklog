#!/usr/bin/env python3
"""Build a throwaway dataset of invented sessions so the app can be shown
(screenshots, docs, trying it out) without exposing anyone's real work.

    ./demo/make_demo.py            # writes to $TMPDIR/worklog-demo and prints how to serve it
    ./demo/make_demo.py /some/dir

It fabricates transcripts in the shape catalog.py reads (custom-title, ai-title,
pr-link, user and assistant records), a worklog.jsonl with summaries, labels
and a few threads, and a demo.toml pointing at them. Nothing in it is real.
"""
import datetime
import json
import os
import random
import sys
import uuid

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TMPDIR", "/tmp"), "worklog-demo")
CODE_ROOT = "/Users/demo/code"
PROJ = os.path.join(OUT, "projects", "-Users-demo-code")
NOW = datetime.datetime.now(datetime.timezone.utc)
random.seed(7)

# name, label (or ""), auto label, summary, outcome, tags, people, repos, prs, tickets, hours ago, days long, prompts
SESSIONS = [
    ("checkout-latency", "checkout p95 regression", "Checkout p95 latency regression after cache change",
     "Traced a p95 latency jump on the checkout API to a cache key change that stopped reusing warm entries across regions. Reverted the key format and added a per-region hit-rate panel.",
     "Fix merged in api#412 and deployed; p95 back under 300ms, dashboard panel added.",
     ["backend", "incident"], ["Priya", "Tom"], {"api": 9, "dashboards": 3}, ["api/412", "dashboards/88"], ["OPS-1183"], 3, 2, 64),
    ("billing-bounces", "", "Billing emails bouncing at one provider",
     "Investigated a spike in bounced invoice emails and found one mailbox provider rejecting mail after a DKIM selector rotation. Re-published the selector and added a bounce-rate alert.",
     "DNS fixed and bounce rate normal within an hour; alert shipped in infra#204.",
     ["infra", "support", "incident"], ["Dana"], {"infra": 5}, ["infra/204"], ["SUP-2291"], 9, 1, 31),
    ("ios-login-crash", "iOS login crash on 18.2", "iOS login crash after OS update",
     "Reproduced a crash on the login screen on the newest OS release caused by a keychain API returning a new error code. Added handling and a regression test; coordinated a hotfix build.",
     "Hotfix 4.8.1 submitted; crash-free sessions back to 99.9%.",
     ["mobile", "incident"], ["Lena", "Marcus"], {"mobile-ios": 14}, ["mobile-ios/931"], [], 20, 3, 88),
    ("node-pool-migration", "", "Kubernetes node pool migration plan",
     "Planned the move from the legacy node pool to a new instance family: drain order, pod disruption budgets, and a rollback path. Wrote the runbook and a dry-run script.",
     "Runbook reviewed; migration scheduled for the next maintenance window, nothing applied yet.",
     ["infra", "docs"], ["Tom"], {"infra": 11, "runbooks": 4}, ["runbooks/19"], ["OPS-1201"], 30, 4, 52),
    ("quarterly-metrics", "board deck numbers", "Quarterly metrics for the board deck",
     "Pulled active-user, retention and revenue series for the quarter, reconciled two sources that disagreed on timezone handling, and produced the charts for the deck.",
     "Numbers signed off by finance; charts exported and handed to the deck owner.",
     ["data", "research"], ["Priya", "Aisha"], {"analytics": 7}, [], [], 50, 2, 40),
    ("vendor-security-questionnaire", "", "Vendor security questionnaire answers",
     "Drafted answers to a customer's security questionnaire from existing policy docs, flagging three questions that needed a real answer from the infra team rather than boilerplate.",
     "Draft sent to the customer success lead with the three open items marked.",
     ["security", "docs"], ["Dana"], {}, [], ["SEC-77"], 70, 1, 22),
    ("onboarding-sam", "Sam onboarding", "New engineer onboarding checklist",
     "Walked through access, repos and first tasks for a new backend engineer, and turned the ad hoc notes into a reusable onboarding checklist.",
     "Checklist merged to the handbook; Sam has access to everything on the list.",
     ["people", "docs"], ["Sam"], {"handbook": 6}, ["handbook/41"], [], 100, 2, 27),
    ("search-relevance", "", "Search relevance tuning experiment",
     "Set up an offline evaluation for search ranking changes using a sample of logged queries, then tested two boosts for recency and exact-title matches.",
     "Recency boost improved the metric and is behind a flag at 10%; exact-title boost was neutral and dropped.",
     ["backend", "data"], ["Marcus"], {"search": 12, "analytics": 2}, ["search/77", "search/79"], [], 140, 5, 96),
    ("cost-review", "cloud bill review", "Monthly cloud bill review",
     "Reviewed the month's bill line by line, found an orphaned test cluster and a log retention setting far above policy, and sized the saving from fixing both.",
     "Cluster deleted and retention cut to 30 days; about 12% off next month's bill.",
     ["costs", "infra"], ["Tom", "Aisha"], {"infra": 3}, ["infra/211"], ["OPS-1190"], 200, 1, 19),
    ("feature-flags-cleanup", "", "Stale feature flag cleanup",
     "Listed every flag older than six months with its evaluation count, removed the dead ones from code and the flag service, and documented the rule for future flags.",
     "Nine flags removed across three services; cleanup rule added to the engineering handbook.",
     ["tooling", "backend"], [], {"api": 6, "web": 4, "handbook": 1}, ["api/398", "web/1502"], [], 260, 3, 45),
    ("support-escalation", "", "Customer export stuck in queue",
     "Chased a customer's data export that had sat in the queue for two days; a retry loop was re-enqueueing it behind newer jobs. Fixed the priority and re-ran the export.",
     "Export delivered; queue priority fix in api#405.",
     ["support", "incident"], ["Dana", "Lena"], {"api": 4}, ["api/405"], ["SUP-2270"], 330, 1, 18),
    ("design-review-notes", "", "API versioning design review",
     "Prepared for and took notes in the design review for API versioning: header versus path, deprecation windows, and how clients discover the current version.",
     "Decision: path versioning with a 12-month deprecation window; ADR to follow.",
     ["backend", "docs"], ["Priya", "Marcus", "Aisha"], {"docs": 2}, [], [], 420, 1, 24),
]

PROMPTS = [
    "can you look into why this started happening yesterday afternoon?",
    "what changed in the last deploy that could explain it",
    "show me the numbers for the last 7 days",
    "ok make the fix but keep it small, I want to be able to revert",
    "write up what we found for the team",
    "that looks right, open a PR",
    "add a test for the case that broke",
    "what would you do differently next time?",
    "summarise where we are so I can hand this off",
    "is there a dashboard for this already?",
    "let's also check the other region",
    "good, mark it done",
]


def ts(hours_ago):
    return (NOW - datetime.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def transcript(sid, name, ai_title, repos, prs, tickets, hours_ago, days, n_prompts):
    lines = []
    span = max(days * 24, 1)
    start = hours_ago + span
    lines.append({"type": "custom-title", "customTitle": name, "sessionId": sid})
    lines.append({"type": "ai-title", "aiTitle": ai_title, "sessionId": sid})
    repo_list = [r for r, n in repos.items() for _ in range(n)] or ["notes"]
    for i in range(n_prompts):
        at = start - span * i / max(n_prompts - 1, 1)
        text = random.choice(PROMPTS)
        if i == 0:
            text = "I need help with %s. %s" % (ai_title.lower(), text)
        if tickets and i == 1:
            text += " (ticket %s)" % tickets[0]
        lines.append({"type": "user", "isSidechain": False, "timestamp": ts(at), "cwd": CODE_ROOT,
                      "gitBranch": "main", "sessionId": sid,
                      "message": {"role": "user", "content": text}})
        repo = random.choice(repo_list)
        lines.append({"type": "assistant", "isSidechain": False, "timestamp": ts(at - 0.01), "sessionId": sid,
                      "message": {"role": "assistant", "content": [
                          {"type": "tool_use", "name": "Edit",
                           "input": {"file_path": "%s/%s/src/%s.py" % (CODE_ROOT, repo, random.choice(["main", "jobs", "api", "util"]))}}]}})
        if prs and i == n_prompts // 2:
            for pr in prs:
                owner_repo, num = pr.split("/")
                lines.append({"type": "pr-link", "sessionId": sid, "prNumber": int(num),
                              "prUrl": "https://github.com/pebble-inc/%s/pull/%s" % (owner_repo, num),
                              "prRepository": "pebble-inc/%s" % owner_repo, "timestamp": ts(at)})
    lines.append({"type": "assistant", "isSidechain": False, "timestamp": ts(hours_ago), "sessionId": sid,
                  "message": {"role": "assistant", "content": [
                      {"type": "text", "text": "Done. The change is merged and deployed; I added a note to the runbook and the dashboard shows the metric back in range. Nothing else is pending on this."}]}})
    return lines


def main():
    os.makedirs(PROJ, exist_ok=True)
    events = []
    threads = []
    for n, (name, label, auto, summary, outcome, tags, people, repos, prs, tickets, hours_ago, days, n_prompts) in enumerate(SESSIONS):
        sid = str(uuid.UUID(int=random.getrandbits(128)))
        with open(os.path.join(PROJ, sid + ".jsonl"), "w") as f:
            # compact separators: catalog.py matches records by substring, as
            # Claude Code writes them, so '"type": "user"' with a space is invisible to it
            for r in transcript(sid, name, auto, repos, prs, tickets, hours_ago, days, n_prompts):
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        fields = {"auto_label": auto, "summary": summary, "outcome": outcome, "auto_tags": tags,
                  "people": people, "enriched": n_prompts}
        if label:
            fields["label"] = label
        events.append({"op": "smeta", "sid": sid, "fields": fields, "ts": ts(hours_ago - 0.1)})
        if n in (0, 2, 3):
            threads.append((name, label or auto, tags[:1]))
    for i, (name, title, tags) in enumerate(threads, 1):
        wid = "w%d" % i
        events.append({"op": "add", "id": wid, "title": title, "prio": ["P1", "P0", "P2"][i - 1], "ts": ts(40 - i)})
        events.append({"op": "set", "id": wid, "fields": {"tags": tags, "status": ["active", "waiting", "active"][i - 1]}, "ts": ts(40 - i)})
        events.append({"op": "sess", "id": wid, "session": "work/" + name, "ts": ts(40 - i)})
        events.append({"op": "note", "id": wid, "text": "picked this up again after standup", "session": "work/" + name, "ts": ts(30 - i)})
    with open(os.path.join(OUT, "worklog.jsonl"), "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    with open(os.path.join(OUT, "demo.toml"), "w") as f:
        f.write('[paths]\nprojects = "%s"\ncode_root = "%s"\nlog = "%s"\ncache = "%s"\n\n[server]\nport = 8790\n\n'
                '[user]\nnames = ["demo"]\n\n[github]\norg = "pebble-inc"\n\n[enrich]\nauto = false\n'
                % (os.path.join(OUT, "projects"), CODE_ROOT, os.path.join(OUT, "worklog.jsonl"),
                   os.path.join(OUT, "sessions-cache.json")))
    print("demo data in %s\n\n  WL_CONFIG=%s ./serve.py -o     # port 8790\n" % (OUT, os.path.join(OUT, "demo.toml")))


if __name__ == "__main__":
    main()
