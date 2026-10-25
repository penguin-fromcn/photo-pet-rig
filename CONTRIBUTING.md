# Contributing

Thanks for helping improve photo-pet-rig.

## Ground rules

- **Never add private photos, faces, alphas, `project.json` files from users, or generated rigs**
  to commits, issues, screenshots or tests.
- Use the synthetic mannequin under `examples/mannequin/` for examples and CI.
- Keep the build deterministic. The Python builder must not call a generative image model.
- Preserve the face contract: source RGB inside `face.polygon` must remain unchanged.
- Do not “fix” failed QA by deleting checks. Fix the alpha, polygons or code.

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

Optional manual smoke test:

```bash
photopet build examples/mannequin/project.json --out ./output
```

## Pull requests

- Keep changes focused.
- Add or update tests when changing the schema, builder, QA or CLI.
- Update `prompts/PROJECT_SCHEMA.md` if a field or validation rule changes.
- Run the test suite before submitting.
- If you change output formats, update `README.md` and `prompts/AGENT.md`.
- Do not bump the schema string unless the change is intentionally breaking.

## Reporting bugs

Please include:

- OS and Python version;
- command used;
- traceback;
- whether the input was a synthetic example or your own photo (never attach a private photo;
  use a redacted/synthetic reproduction);
- the relevant part of `qa_report.json` without personal data.
