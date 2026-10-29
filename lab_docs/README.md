# Lab docs: how to write them

> Owner: designer (format) + PM / lab officers (facts).
> This folder is the assistant's **only** source of lab facts (brief §5.3). If it
> isn't written here, the assistant says "I'm not sure, ask a lab member".

## How to add a real doc

1. Copy a template: `cp lab_docs/_TEMPLATE_join.md lab_docs/join.md`.
2. Replace every `TODO(PM): ...` with a confirmed fact, **or delete that section**.
   A half-filled section is worse than a missing one.
3. Delete the `<!-- guidance -->` comments.
4. Set the `Last updated:` line to today's date (YYYY-MM-DD).
5. Re-run ingest (M2) so the assistant picks it up.

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
| Keep each file under ~600 words. Split if it grows. | Smaller sections retrieve more precisely. |

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
