---
name: demo-prep
description: Use for Shotgun rehearsal, filming, slides and submission (steps 7.1, 7.2, 7.3), or when asked about the demo script, Devpost tags, Notability screenshots, the Fetch.ai/ASI submission or the deadline. Holds the checklists; Daniel does the in-car parts.
---

# Demo prep

**Deadline: Sun Oct 4, 12:00 PM EDT** (Devpost shows 12:15; don't use the buffer). Judging 12:30 to 3:00 PM at the table. Leave at least 90 minutes for 7.3.

## The 60-second script
Filmed **parked or with a second driver**. Never film Daniel driving while handling the phone.

> Plug in → ring, "Shotgun" on the Civic screen → "Morning. 31-minute drive home. Anything you want handled?" → request → "On it." → later ring → result + "Confirm?" → "Yes."

- **Coding hero (D17, the demo script):** plug in → "What's the Michigan score?" (answered on the call) → "Fix the login bug Sarah filed." → "Merge it if the tests pass?" → "Yes." → "That's all." → arrival call: "The login fix is merged; the tests passed." No email line (3.2 is stretch).
- **Food hero (if switched at hour 10):** "Order my usual ramen so it's there when I get home, email Alex to come over, and fix the login bug Sarah filed." → callback: "Ramen is $21.40 with tip, ordering in 6 minutes so it lands at 7:12. Confirm?" → "Yes."
- PLAN.md's original line says "text Alex", but there's no SMS worker (DECISIONS.md §9.2), so say "email".
- Pitch lines: "Everyone put a chatbot in the car. Shotgun is an agent built for the car." / "Your agent isn't an app. It's a contact."
- Car-specific points: the car starts the agent (plug-in); arrival time is the deadline; no screen means short answers, spoken confirmation, background work.

## 7.1 Rehearsal checklist (Daniel in the Civic; pass = 3 clean runs back to back)
- [ ] Phone charged, wired CarPlay cable, Do Not Disturb **off** for the Shotgun contact, volume up.
- [ ] Railway healthy (`make smoke`); `railway logs` open on the laptop.
- [ ] Job table clean: no stale `needs_approval`/`exception` rows or open drives that would land in the arrival call.
- [ ] Demo repo reset: planted login bug present, no open Claude PRs or branches.
- [ ] Both Shortcut automations on with Run Immediately; the Shotgun contact is in Favorites.
- [ ] Fallback ready: tap the Shotgun contact if the plug-in ring fails.
- [ ] Pre-recorded successful hero run on the laptop in case the live one fails.
- [ ] Time each run: plug-in → ring (target ≤ 10 s), arrival-call delay.

## 7.2 Filming
- Main take plus a **backup take**, saved in two places (not in git).
- Show the CarPlay screen with "Shotgun", and be heard saying "Yes".
- Cut a **3 to 5 minute** version for Fetch.ai that includes an ASI:One chat creating a job.

## 7.3 Slides, Devpost, ASI submission
**Devpost** (https://mhacks-2026.devpost.com): main track **Actually Intelligent (AI)**, only one main track. Opt into every eligible sponsor prize:
- [ ] **ElevenLabs:** tag ElevenLabs; covers both "Best Project Built with ElevenLabs" and "[MLH] Best Use of ElevenLabs".
- [ ] **Neon:** "Best Use of Neon Backend"; describe the job table.
- [ ] **Notability:** tag Notability with **a note on how it was used and at least 2 screenshots** of the sketches and wireframes from step 0.3.
- [ ] **Fetch.ai ASI:One Agent Challenge.**
- [ ] Public GitHub link, video link, "built with" list, safety paragraph (voice only, spoken confirmation for every irreversible action, long items wait until parked, filmed parked).

**Fetch.ai / ASI Submission Agent** (separate from Devpost, required):
- [ ] README has the agent name and address and both badges (`tag:innovationlab`, `tag:hackathon`).
- [ ] Agent is live on Agentverse (mailbox) and findable in ASI:One; save an ASI:One shared-chat URL.
- [ ] In ASI:One, message the **MHacks Submission Agent** ("Hi") → "Create team (I'm the lead)". Give the project name, Daniel's name and email, team size 1, problem solved, public GitHub URL, plus table number, video URL, Agentverse profile URL and shared-chat URL. Save the Team ID (`mhacks-...`). Status must read "Submitted". Dashboard: https://asi1.ai/festival/mhacks2026/dashboard

**Slides** (about 5): the problem (chatbots in cars) → Shotgun is a contact → live or video demo → architecture (Notability sketch) → safety story → sponsor tech.
