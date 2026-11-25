# 0001 — Phase 1 decisions

Date: 2026-11-25. Decided by: Joseph.

| Decision | Choice | Reason |
| --- | --- | --- |
| Second data source | Use Hardcover | Reader tags are the best free trope signal; free account and token |
| AI classification | Claude Haiku via Batch API | Fills gaps tags miss; estimated under $1 for 500 books; quotes checked against stored text |
| Repository visibility | Private for now | Personal project first; can be made public later with one click. Visibility affects the code only, not who can use the app |
| Accuracy benchmark | Joseph hand-labels ~20 books × 10 tropes | Honest measure of classifier accuracy before scaling |
| Google Books | Optional enrichment only | Its terms forbid charging users without Google permission, which conflicts with a possible premium tier |
| Taxonomy storage | YAML file in the repo, synced to the database | Readable, editable and reviewable in Git |
| Database changes | Numbered SQL migration files | The database can be rebuilt exactly from the repo |
