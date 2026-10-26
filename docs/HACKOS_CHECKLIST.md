# HackOS checklist

Open Agent Hackathon 2026. Repository `Fu93/phoenixrepo`. Solo team PhoenixRepo. All submitted text is in English.

- [ ] Select **Track 02** ("The Agent That Can Explain Why").
- [ ] Add the submission: repository link, project description from `docs/SUBMISSION_DRAFT.md` section (ii), and the declarations in sections (iii) and (iv).
- [ ] Video length **1 to 4 minutes**. The shot list in `docs/VIDEO_SCRIPT.md` is cut for 3:00.
- [ ] Declare the pre-event skeleton: commits up to and including `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000). Everything after that commit is build-window work.
- [ ] First submission by **27 Oct 2026 12:00 UTC**.
- [ ] Final submission by **27 Oct 2026 21:00 UTC**.
- [ ] Hard deadline **27 Oct 2026 23:45 UTC**.

Before the final submit, on the commit you submit: `python scripts/fill_results.py logs/eval-nemotron-v3.json --write`, then commit. It fills every numbers slot in `README.md` and `docs/SUBMISSION_DRAFT.md` from the report and the traces, and leaves TBD where a file has no number.
