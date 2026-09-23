# Test fonts

Local copies of the four families `index.html` loads from Google Fonts
(Inter, JetBrains Mono, Space Grotesk, Source Serif 4). Only the render
suite (`test_frame_render.py`) uses them. Its browser serves them from
here, so text is measured with the real typefaces without the test
reaching the internet.

- `fonts.css` is the `latin` and `latin-ext` faces of the stylesheet Google
  serves for the exact URL in `index.html`. Its `url()`s are left as the
  `fonts.gstatic.com` addresses; the harness maps each one to the file here
  whose name is that path with `/` turned into `_`.
- The files are unchanged from `fonts.gstatic.com`.

Every family is under the SIL Open Font License 1.1. Each family's license,
with its copyright line, is in `OFL-<family>.txt`.

If `index.html` changes its font URL, refetch with a Chrome user agent, so
that Google answers with woff2, and replace these files.
