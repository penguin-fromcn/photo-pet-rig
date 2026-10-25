# Security and privacy

## Personal photos are sensitive

This tool is designed for photos of real people. Only process photos that you own or
have explicit permission to use. Do not upload someone else's photo or generated rig
to a public issue, repository, demo or dataset.

The repository's `.gitignore` excludes common local input/output paths, but that is a
convenience, not a security boundary. Check your own commits before pushing.

## Network behavior

- The core builder (`photopet build`) runs locally and does not upload photos.
- `photopet cutout` uses the optional `rembg` dependency. On first use, rembg downloads
  model weights. Review rembg and the model provider's terms.
- If you use a cloud AI agent to inspect the photo, the photo is sent to that provider.
  This repository cannot control or protect data after it leaves your machine. Use a
  local vision model/agent if the photo must remain private.

## Reporting a vulnerability

Open a minimal private report to the repository maintainers with:

- affected version/commit;
- a reproducer that does **not** contain a real person's photo;
- expected and actual behavior;
- whether the issue involves unexpected network access, path traversal, or data leakage.

Do not post private images in a public issue.
