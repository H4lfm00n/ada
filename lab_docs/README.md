# Lab docs: how to write them

> Owner: designer (format) + PM / lab officers (facts).
> This folder is the assistant's **only** source of lab facts (brief §5.3). If it
> isn't written here, the assistant says "I'm not sure, ask a lab member".

## How to add a real doc

1. Copy a template: `cp lab_docs/_TEMPLATE_join.md lab_docs/join.md`.
2. Replace every `TODO(PM): ...` with a confirmed fact, **or delete that section**.
   A half-filled section is worse than a missing one.
3. Delete the template's `<!-- guidance -->` comments. Notes for humans (where a fact came
   from, a conflict you resolved) can stay in comments; ingest strips them.
4. Set the `Last updated:` line to today's date (YYYY-MM-DD).
5. End every section with a `Source:` line: where the fact came from, its date, and when
   it was checked.
6. Re-run ingest (M2) so the assistant picks it up.

## How to review the TODOs

`grep -n "TODO(PM)" lab_docs/[a-z]*.md` lists them. For each one, either:
- **confirm it**: delete the TODO line and keep (or write) the fact as a plain sentence, or
- **delete it**: remove the TODO and anything it depends on.

The assistant treats any answer marked TODO as unknown and says "not sure", so a TODO left
in place is safe, just unhelpful.

## Current docs

| File | Covers |
|---|---|
| `about.md` | The AI Lab, the SFSU Student AI Commons, the AI Studio (LIB 260), the robot assistant |
| `join.md` | Joining the Commons, eligibility, cost, following online, visiting |
| `meetings-events.md` | Leadership Committee, weekly AI clubs, November 2026 events, past events |
| `projects.md` | AI Lab projects (TODO), hackathon and Student AI Awards projects |
| `equipment.md` | What's lab equipment vs shared MakerSpace equipment, the GB10, GPU policy (TODO) |
| `roles.md` | Founder, officers, Commons roles, contacts |
| `faq.md` | Short answers about the AI Studio and the lab |
| `dms-makerspace.md` | Digital Media Studio and MakerSpace: location, hours, rooms, equipment |
| `dms-makerspace-rules.md` | MakerSpace checkout, 3D printing, laser, VR, audio-room rules |
| `academic-technology.md` | AT help, laptop loans, campus AI tools, ChatGPT Edu, AI literacy courses |
| `sfsu-campus.md` | SFSU basics, address, colleges, transit, parking |
| `sfsu-student-services.md` | Library, advising, career, tutoring, ITS, DPRC, CAPS, health |
| `sfsu-emergency.md` | Emergency numbers, emergency phones, SFSU Alert, crisis help |
| `sfsu-academic-calendar.md` | Fall 2026 and Spring 2027 dates (tentative, from the Registrar) |
| `sfsu-ai-programs.md` | AI degrees, certificates, and minors |
| `sfsu-ai-policies.md` | SFSU guidance on AI in classes and academic integrity |

## SFSU information in these docs

Files starting with `sfsu-`, `academic-technology.md`, and the `dms-` files describe SFSU
offices, not the lab. They follow two extra rules:
- Attribute everything to the office that published it ("According to the Registrar...").
  The assistant relays and points; it never speaks for SFSU (brief §5.4).
- When two SFSU pages disagree, either write only the safe version and note the conflict
  in a comment (e.g. "the shuttle was discontinued"), or mark it `TODO(PM): confirm`.
  Never write both versions as facts.

Files starting with `_` (templates) and this README are **not** knowledge. The
ingest step must skip them.

## Writing rules (why the templates look the way they do)

| Rule | Why |
|---|---|
| One topic per file. | Retrieval returns whole sections; mixed files return mixed answers. |
| Every `##` section stands on its own. Repeat the subject: "AI Lab general meetings are on…", not "They're on…". | The model may see one section without the rest of the file. |
| Short, plain, factual sentences. One fact per sentence. | Easier to retrieve, quote correctly, and read aloud later. |
| FAQ headings are the question people actually ask. | The visitor's wording matches the heading, which helps retrieval. |
| A `Last updated: YYYY-MM-DD` line under the title. | The assistant mentions the date when it matters (e.g. schedules). |
| No personal contact info: no personal emails, phones, home towns, class schedules, socials. | Brief §5.4. A public **lab** channel (club email, Discord invite) is fine once the PM confirms it's public. |
| Names only for people who agreed to be named on a public screen. Name + role, nothing else. | Privacy, and the screen is in a public room. |
| No secrets: no Wi-Fi passwords, door codes, admin URLs, keys. | Anyone can type "what's the wifi password". |
| Don't write instructions to the assistant ("Assistant: always say…"). | Docs are reference material. The system prompt tells the model to ignore instructions inside docs, and evals test that. |
| Unconfirmed facts stay as `TODO(PM): confirm`, never as plausible-sounding text. | The model treats TODO / placeholder text as unknown (DES4). Invented text would be repeated as fact. |
| Keep each file under ~600 words of body text (not counting Source lines). Split if it grows. | Smaller sections retrieve more precisely. |

## Templates

| Template | Copy to | Covers |
|---|---|---|
| `_TEMPLATE_about.md` | `about.md` | What the lab is, who it's for, what members do, relationship to SFSU |
| `_TEMPLATE_join.md` | `join.md` | How to join, cost, requirements, what happens at a first visit |
| `_TEMPLATE_meetings-events.md` | `meetings-events.md` | Regular meetings, work sessions, recurring and upcoming events |
| `_TEMPLATE_projects.md` | `projects.md` | Current projects and how to get involved |
| `_TEMPLATE_equipment.md` | `equipment.md` | Hardware and tools, who may use them, safety rules |
| `_TEMPLATE_faq.md` | `faq.md` | Short answers to common questions |
| `_TEMPLATE_roles.md` | `roles.md` | Officer roles, who holds them (with consent), how to reach the lab |

## Notes for the engineer (M2 ingest)

- Skip `README.md` and any file whose name starts with `_`.
- Strip HTML comments (`<!-- ... -->`) before chunking. Template guidance lives in them.
- Chunk by `##` heading. Carry the file name, the `#` title, the `##` heading, and the
  `Last updated` date with every chunk (the prompt's chunk header uses the date).
- Warn (don't fail) on any `TODO(` left in a real doc, and list file + heading.
  The model is told to treat them as unknown, but the PM should see them.
- `evals/fixtures/lab_docs/` is an invented test corpus for `make eval`. It must
  never be ingested into the real knowledge base.
