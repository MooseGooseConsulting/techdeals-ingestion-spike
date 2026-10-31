# Reference repositories (read-only)

Pinned for reference. These repositories are **not** dependencies and are never
cloned inside this checkout. Consult them by remote URL and the pins below, or in
a sibling directory outside this repo. Do not copy their code, SQL, migrations,
or module trees into this project; absorb ideas only (see
[Directions already rejected](problems.md#directions-already-rejected)).

| Repository | Pin | Role |
|---|---|---|
| [MooseGooseConsulting/TechdealsHandoff](https://github.com/MooseGooseConsulting/TechdealsHandoff/tree/1ac8f887db62420a2c2dc2de2ed41597ee7802c6) | `1ac8f887db62420a2c2dc2de2ed41597ee7802c6` | Production. Eventual consumer of proven improvements. Source of fixtures, ADRs, issues, and the NORTH_STAR. Never changed by this spike. |
| [MooseGooseConsulting/techdeals-v2](https://github.com/MooseGooseConsulting/techdeals-v2/tree/68e106c1ae7e8ba4d4d2bbd67bacc7a8659dd61d) | `68e106c1ae7e8ba4d4d2bbd67bacc7a8659dd61d` | Posting-first experiment (Neon scratch, `engine/extract.py`). Goal statement + shape vocabulary as reference only. |
| [MooseGooseConsulting/techdeals-v2-review-import](https://github.com/MooseGooseConsulting/techdeals-v2-review-import/tree/60eec34a0e227095c29ef5c1f554c4996d913ad3) | `60eec34a0e227095c29ef5c1f554c4996d913ad3` (reviewed extraction branch [`88ff05e9b33dd71a86d80874169f15549c7d0ece`](https://github.com/MooseGooseConsulting/techdeals-v2-review-import/tree/88ff05e9b33dd71a86d80874169f15549c7d0ece)) | Transferred personal history and open PRs. History preserved there; not replayed here. |

Pins recorded 2026-10-24 from the reviewed snapshot listed in
`docs/plans/parsing-usefulness-and-integration.md` of techdeals-v2 (a later
commit than the pin, e.g. `4d7637c7b3a1e642737c6892c564f01b6ec33f37`). Refresh a pin
before citing anything specific from these repositories.

## Why these are not subdirectories here

Nested clones show in `git status` only as one opaque untracked directory, break the clean-checkout demonstration the
[data contract](data-contract.md) requires, and invite accidental code copying.
Keep reference material out of this working tree.
