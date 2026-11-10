# Lab docs: how to write them

> Owner: designer (format) + PM / lab officers (facts).
> This folder is the assistant's **only** source of lab facts (brief §5.3). If it
> isn't written here, the assistant says "I'm not sure, ask a lab member".

## Current docs (v1, 2026-11-10)

| File | Covers |
|---|---|
| `about.md` | The SFSU Student AI Commons, the AI Studio (LIB 260), its opening and hours, CSU AI Commons vs Student AI Commons |
| `join.md` | Join form, Discord and Instagram, Leadership Committee, coordinated AI clubs, visiting |
| `meetings-events.md` | Leadership Committee, weekly AI clubs, where events are announced, past and November 2026 events |
| `projects.md` | Commons projects (none published yet), hackathon and Student AI Awards projects |
| `equipment.md` | Shared MakerSpace equipment useful for AI projects; GPU/remote access (no policy published) |
| `rules.md` | MakerSpace hours and rules: A/V and kit checkout, 3D printing, laser, VR, audio and sewing rooms |
| `roles.md` | Leadership Committee, Coordinator role (no names), public contacts |
| `faq.md` | Short answers about the Commons and the AI Studio |
| `sfsu.md` | SFSU basics, colleges, AI programs, student services, emergency numbers, calendar, transit, parking |
| `academic-technology.md` | AT services, hours, laptop loans, campus AI tools, ChatGPT Edu, AI literacy courses |
| `dms.md` | Digital Media Studio and MakerSpace: location, contact, equipment, rooms, November events |
| `ai-policy.md` | SFSU guidance on AI in coursework, syllabi, employee use, data |

Templates for new docs live in `docs/lab_doc_templates/`, **outside** this folder, so ingest
can never pick them up.

## Rules for ingest (engineer, M2)

- Ingest every `*.md` here except `README.md` and any file starting with `_`.
- Strip HTML comments (`<!-- ... -->`) before chunking. Notes for humans live in them.
- Chunk by `##` heading. Carry the file name, the `#` title, the `##` heading, and the
  `Last updated` date with every chunk. Keep the section's `Source:` line in the chunk.
- **Drop any `##` section that contains `TODO(`**, and log file + heading so the PM sees it.
- `evals/fixtures/lab_docs/` is an invented test corpus. Never ingest it here.

## How TODOs work

A `TODO(PM): confirm` marks a fact the PM still has to answer. Because ingest drops the
whole section, a TODO always sits in **its own** `##` section and never shares a section
with a confirmed fact. Confirmed facts never carry a TODO.

List them: `grep -n "TODO(PM)" lab_docs/[a-z]*.md`. To resolve one, replace the TODO
section with the confirmed fact (or merge it into the related confirmed section), or delete it.

## How to add or change a doc

1. Copy a template from `docs/lab_doc_templates/` (or edit the existing doc).
2. Write only confirmed facts. Anything unconfirmed goes in a separate TODO section, or stays out.
3. Set `Last updated: YYYY-MM-DD` under the title.
4. End every `##` section with a `Source:` line: page, its date, and when it was checked.
5. Re-run ingest (M2) so the assistant picks it up.

## SFSU information in these docs

`sfsu.md`, `academic-technology.md`, `dms.md`, and `ai-policy.md` describe SFSU offices,
not the Student AI Commons. Two extra rules:
- Attribute everything to the office that published it ("According to the Registrar...").
  The assistant relays and points; it never speaks for SFSU (brief §5.4).
- When two SFSU pages disagree, never write both versions as facts. Put the disagreement in
  its own TODO section.

## Writing rules

| Rule | Why |
|---|---|
| One topic per file. | Retrieval returns whole sections; mixed files return mixed answers. |
| Every `##` section stands on its own. Repeat the subject: "The MakerSpace is open…", not "It's open…". | The model may see one section without the rest of the file. |
| Short, plain, factual sentences. | Easier to retrieve, quote correctly, and read aloud later. |
| FAQ headings are the question people actually ask. | The visitor's wording matches the heading, which helps retrieval. |
| A `Last updated: YYYY-MM-DD` line under the title; label past events "(past)". | The assistant mentions dates when they matter. |
| No personal contact info: no personal emails, phones, profiles, class schedules. Office and group addresses are fine. | Brief §5.4. |
| Names only for people who agreed to be named on a public screen. | Privacy, and the screen is in a public room. |
| No secrets: no Wi-Fi passwords, door codes, admin URLs, keys. | Anyone can type "what's the wifi password". |
| Don't write instructions to the assistant ("Assistant: always say…"). | Docs are reference material; the system prompt tells the model to ignore instructions in docs. |
| Aim for under ~600 words per file. `sfsu.md` is longer by PM request; its sections stand alone. | Smaller sections retrieve more precisely. |
