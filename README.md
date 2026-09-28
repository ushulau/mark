# mark (Python clone)

Sync Markdown files to Confluence. This is a dependency-free Python port of
[mark](mark-research/README.md): point it at Markdown files, and it creates or
updates the matching Confluence pages — including parents, attachments,
images, and labels. No third-party packages are required.

## Install

```sh
pip install -e .
```

This provides both entry points (the same command):

```sh
mark sync --files "docs/**/*.md" ...
python -m mark sync --files "docs/**/*.md" ...
```

For upstream muscle memory, the `sync` subcommand may be omitted:
`mark --files "docs/**/*.md" ...` works too.

## Quick start

1. Add headers to the top of a Markdown file:

```markdown
<!-- Space: TEST -->
<!-- Title: My Article -->

# My Article

Body text.
```

2. Sync it:

```sh
mark sync --files page.md --base-url https://tenant.atlassian.net/wiki \
  --username me@example.com --password <api-token>
```

Use a Confluence API token as the password (Atlassian account → Security →
API tokens). To preview without touching Confluence, add `--compile-only`
(no credentials needed) or `--dry-run`.

## How to use

### Finding pages

Each document names its page with leading HTML headers (or YAML front
matter, see below):

```markdown
<!-- Space: TEST -->
<!-- Title: My Article -->
<!-- Parent: Guide -->
```

| Header | Meaning |
|---|---|
| `Space` | Space key (or `--space`) |
| `Title` | Page title (or `--title-from-h1` / `--title-from-filename`) |
| `Parent` | Repeatable parent titles (`--parents` are prepended) |
| `Type` | `page` (default) or `blogpost` |
| `Layout` | `article` wraps the body in a two-column layout |
| `Sidebar` | Sidebar content; implies `article` layout |
| `Emoji` | Page emoji, set on update |
| `Attachment` | Repeatable local file/glob to upload |
| `Label` | Repeatable page labels |
| `Image-Align` | `left`, `center`, `right` (overrides `--image-align`) |
| `Content-Appearance` | `full-width`, `fixed`, `default` |
| `Order` | Must be a whole number; recorded but sibling reordering is not applied |
| `Folder` | Accepted with a warning; folders are unsupported |
| `Property: key=value` | Accepted with a warning; content properties are unsupported |
| `Synchronized: false` | Skip this file |
| `Include` | Accepted with a warning; includes are not expanded |

A nearly-valid header (e.g. `Titel:`) is an error; headers below page
content are ignored with a warning.

Equivalent YAML front matter (plural names for repeatables) is always
parsed; HTML headers override scalar values and append to repeatable ones:

```markdown
---
space: TEST
title: My Article
parents: [Guide]
labels: [docs]
---

# My Article
```

Only the YAML subset these keys need is supported (scalars, block/flow
lists, one mapping level for `properties`).

Alternatively, `--target-url/-l '...?pageId=123'` publishes straight to one
page: file metadata is ignored with a warning, and `--base-url` defaults to
the URL's host when not given.

`--parents-from-path` derives parents from each file's directories relative
to the `--files` glob root (override with `--parents-from-path-root`):
`docs/Guide/Sub/page.md` sits under `Guide / Sub`. `index.md`/`README.md`
stands for its own directory. Documents naming their own `Parent` are left
alone.

### Previewing

- `--compile-only` prints the storage HTML for each file and touches
  nothing (no credentials needed).
- `--dry-run` resolves pages and ancestry against Confluence, prints what
  would happen plus the storage HTML, and writes nothing: no pages,
  parents, attachments, or labels are created.

### Publishing behaviour

- Missing parents are created; existing pages are updated in place.
- Unchanged pages are skipped instead of re-uploaded (upstream does this
  only with `--changes-only`). With `--changes-only`, a SHA-1 content hash
  is also stored in the version message (`[v<hash>] note`) and compared
  first, matching upstream's format.
- `--minor-edit` suppresses notifications; `--version-message` annotates
  the new version; `--content-appearance` sets the page width.
- `--continue-on-error` keeps going after a failing file; `--ci` also
  tolerates globs matching nothing.
- `--output-format` selects the run report: `url` (default, human lines),
  `json` (`{"pages": [...], "errors": [...]}`), or `github` (workflow
  commands).
- Exit code is `0` when every file synced, `1` when any file failed
  (including partial runs with `--continue-on-error`).

### Attachments

Three sources, all uploaded with skip-if-identical (sha256) semantics:

1. `Attachment` headers / `attachments` front matter (globs allowed,
   relative to the file).
2. Local images (`![alt](img.png)`) — always attached and rewritten to
   `ri:attachment` references.
3. With `--attach-referenced`, links to local files are attached and
   rewritten the same way; without it they are published as written.

Missing referenced files warn and are skipped; a missing `Attachment`
pattern fails the file.

### Configuration

Precedence is CLI flag > `MARK_*` environment variable > config-file key.
The config file is TOML at `~/.config/mark.toml` (override with
`--config`/`MARK_CONFIG`); every flag below has a matching variable and
key, e.g. `--base-url` / `MARK_BASE_URL` / `base-url`:

```toml
username = "me@example.com"
password = "api-token"
base-url = "https://tenant.atlassian.net/wiki"
title-from-h1 = true
drop-h1 = true
```

```sh
export MARK_BASE_URL=https://tenant.atlassian.net/wiki
export MARK_USERNAME=me@example.com
export MARK_PASSWORD=<api-token>
```

Environment booleans accept `1/true/yes/on` and `0/false/no/off`;
list options (`--files`, `--check-links`, `--features`) take
comma-separated values. Unknown keys/variables warn and are ignored.

## Parameters

### Connection and authentication

- `--base-url, -b URL` (`MARK_BASE_URL`, `base-url`, default: none) —
  Confluence base URL, e.g. `https://tenant.atlassian.net/wiki`. Required
  unless `--compile-only` is used (or `--target-url` supplies a host).
  URLs on `api.atlassian.com` fail fast: the gateway needs the v2 API,
  which this client does not implement.
- `--username, -u NAME` (`MARK_USERNAME`, `username`) — Confluence
  username (usually your email). With a username, authentication is HTTP
  Basic; a token *without* a username is sent as a Bearer token (PAT).
- `--password, -p TOKEN` (`MARK_PASSWORD`, `password`) — API token or
  password. `-p -` reads the token from the first line of stdin. Required
  for every run except `--compile-only`. Mutually exclusive with
  `--password-command`.
- `--password-command CMD` (`MARK_PASSWORD_COMMAND`, `password-command`) —
  helper command whose first stdout line is the token (run without a shell
  and without stdin; a non-zero exit fails the run). Mutually exclusive
  with `--password`.
- `--target-url, -l URL` (`MARK_TARGET_URL`, `target-url`) — publish every
  file straight to the page selected by the URL's `pageId` query
  parameter; per-file metadata is ignored with a warning. When
  `--base-url` is empty it defaults to the URL's `scheme://host[:port]`.
  A `--target-url` without `pageId` is an error (except `--compile-only`).
- `--insecure-skip-tls-verify` (`MARK_INSECURE_SKIP_TLS_VERIFY`,
  `insecure-skip-tls-verify`, default: off) — skip TLS certificate
  verification.

### What to sync

- `--files, -f PATTERN...` (`MARK_FILES`, `files`) — Markdown files to
  sync; each value is a path or glob pattern (quote globs so the shell
  does not expand them). At least one file must match, unless `--ci` is
  given.
- `--space KEY` (`MARK_SPACE`, `space`) — default space key for documents
  without a `Space` header.
- `--config, -c PATH` (`MARK_CONFIG`, no file key) — TOML config file to
  read instead of `~/.config/mark.toml`. A missing default file is fine;
  a missing explicitly-named file is an error.
- `--version` — print the version and exit.

### Titles and parents

- `--parents "A/B"` (`MARK_PARENTS`, `parents`) — parent pages prepended
  to each document's own `Parent` headers, split on
  `--parents-delimiter`.
- `--parents-delimiter DELIM` (`MARK_PARENTS_DELIMITER`,
  `parents-delimiter`, default: `/`) — delimiter for `--parents` (and for
  `parents` values coming from config/env).
- `--parents-from-path` (`MARK_PARENTS_FROM_PATH`, `parents-from-path`,
  default: off) — place each page under pages named after its directories
  (see "Finding pages"). Ignored for documents that declare their own
  `Parent` and in `--target-url` mode.
- `--parents-from-path-root DIR` (`MARK_PARENTS_FROM_PATH_ROOT`,
  `parents-from-path-root`) — directory `--parents-from-path` measures
  from; default is the `--files` glob root.
- `--title-from-h1` (`MARK_TITLE_FROM_H1`, `title-from-h1`, default: off)
  — take the page title from the leading `#` heading. Mutually exclusive
  with `--title-from-filename`.
- `--title-from-filename` (`MARK_TITLE_FROM_FILENAME`,
  `title-from-filename`, default: off) — take the page title from the
  file name (`my-page.md` → `my page`). Mutually exclusive with
  `--title-from-h1`.
- `--title-append-generated-hash` (`MARK_TITLE_APPEND_GENERATED_HASH`,
  `title-append-generated-hash`, default: off) — append a short
  parents/space/title hash to each title.
- `--drop-h1` (`MARK_DROP_H1`, `drop-h1`, default: off) — do not publish
  the first `#` heading (handy with `--title-from-h1`).

### Rendering

- `--strip-linebreaks, -L` (`MARK_STRIP_LINEBREAKS`, `strip-linebreaks`,
  default: off) — accepted for compatibility; the renderer never emits
  raw linebreaks, so this changes nothing.
- `--image-align ALIGN` (`MARK_IMAGE_ALIGN`, `image-align`) — default
  image alignment: `left`, `center`, or `right`. A per-file `Image-Align`
  header wins; anything else is an error.
- `--content-appearance MODE` (`MARK_CONTENT_APPEARANCE`,
  `content-appearance`) — default page width: `full-width`, `fixed`, or
  `default`. Anything else is an error.
- `--attach-referenced` (`MARK_ATTACH_REFERENCED`, `attach-referenced`,
  default: off) — upload local files that links point at as attachments
  and rewrite the links to `ri:attachment` references.

### Previewing and publishing

- `--compile-only` (`MARK_COMPILE_ONLY`, `compile-only`, default: off) —
  print the resulting storage HTML per file (`--- path -> SPACE / Title
  ---` plus the HTML) without touching Confluence. Needs no base URL or
  credentials.
- `--dry-run` (`MARK_DRY_RUN`, `dry-run`, default: off) — resolve pages
  and ancestry against Confluence, print what would happen plus the
  storage HTML, and write nothing.
- `--minor-edit` (`MARK_MINOR_EDIT`, `minor-edit`, default: off) — mark
  updates as minor edits (no notifications).
- `--version-message MSG` (`MARK_VERSION_MESSAGE`, `version-message`) —
  text for the new page version. With `--changes-only` it is prefixed
  with the content-hash stamp (`[v<sha1>] MSG`).
- `--changes-only` (`MARK_CHANGES_ONLY`, `changes-only`, default: off) —
  skip pages whose stored content hash already matches; the hash is kept
  in the version message in upstream's format. (Unchanged pages are
  skipped even without this flag; the flag adds the hash comparison.)
- `--append-labels` (`MARK_APPEND_LABELS`, `append-labels`, default: off)
  — add the document's labels without removing existing ones. By default
  the page ends up with exactly the document's labels (no `Label` headers
  means all labels are removed).

### Run control, output, logging

- `--ci` (`MARK_CI`, `ci`, default: off) — do not fail when file patterns
  match nothing (exits 0; with `--output-format json` prints
  `{"pages": [], "errors": []}`).
- `--continue-on-error` (`MARK_CONTINUE_ON_ERROR`, `continue-on-error`,
  default: off) — keep processing remaining files after a failure. The
  exit code is still `1` if anything failed.
- `--output-format FMT` (`MARK_OUTPUT_FORMAT`, `output-format`, default:
  `url`) — run report: `url` (human `created/updated/unchanged …` lines),
  `json` (`{"pages": [...], "errors": [...]}`), or `github` (`::error` /
  `::notice` workflow commands).
- `--log-level LEVEL` (`MARK_LOG_LEVEL`, `log-level`, default: `info`) —
  `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `FATAL` (any case).
- `--color WHEN` (`MARK_COLOR`, `color`, default: `auto`) — accepted for
  compatibility (`auto` or `never`); output is plain text either way.

### Accepted but not implemented

These flags parse, validate, and warn at runtime — then change nothing.
They exist for upstream command-line compatibility:

- `--edit-lock, -k` (`MARK_EDIT_LOCK`) — page edit locking.
- `--no-overwrite` (`MARK_NO_OVERWRITE`) — refuse to overwrite newer
  pages. Requires `--track-pages`.
- `--track-pages` (`MARK_TRACK_PAGES`) — page-manifest tracking.
- `--manifest-page PAGE` (`MARK_MANIFEST_PAGE`) — manifest storage page.
  Requires `--track-pages`.
- `--manifest-prefix PREFIX` (`MARK_MANIFEST_PREFIX`, default:
  `mark.manifest`) — manifest property prefix (letters, digits, `_`, `-`,
  dots). A non-default value requires `--track-pages`.
- `--on-orphan ACTION` (`MARK_ON_ORPHAN`, default: `report`) — `report`,
  `archive`, or `delete`. Anything but `report` requires `--track-pages`.
- `--orphan-under PAGE` (`MARK_ORPHAN_UNDER`) — limit orphan scope.
- `--preserve-comments` (`MARK_PRESERVE_COMMENTS`) — keep inline comments
  on update.
- `--check-links SCOPE...` (`MARK_CHECK_LINKS`) — fail on unresolvable
  links; any of `internal`, `confluence`, `external`, `all`. Repeatable
  and comma-separable.
- `--check-links-warn-only` (`MARK_CHECK_LINKS_WARN_ONLY`) — report bad
  links without failing. Requires `--check-links`.
- `--global-properties FILE` (`MARK_GLOBAL_PROPERTIES`) — YAML/JSON
  content-properties file.
- `--include-path DIR` (`MARK_INCLUDE_PATH`) — fallback directory for
  `Include`.
- `--features FEAT...` (`MARK_FEATURES`) — optional render features;
  known values: `d2`, `date`, `emoji`, `frontmatter`,
  `inline-link-card`, `math`, `mention`, `mermaid`,
  `mkdocsadmonitions`, `plantuml`. Repeatable and comma-separable.
  (`frontmatter` is always enabled in this clone.)
- `--mermaid-scale N` (`MARK_MERMAID_SCALE`, default: `1.0`),
  `--mermaid-engine chrome|merman` (`MARK_MERMAID_ENGINE`, default:
  `chrome`), `--mermaid-output png|svg` (`MARK_MERMAID_OUTPUT`, default:
  `png`), `--mermaid-bundle` (`MARK_MERMAID_BUNDLE`) — mermaid rendering.
  Scales must be finite numbers above 0; `--mermaid-bundle` needs
  `--mermaid-output=svg`.
- `--math-format png|svg` (`MARK_MATH_FORMAT`, default: `png`),
  `--math-scale N` (`MARK_MATH_SCALE`, default: `2.0`) — math rendering.
- `--d2-output png|svg` (`MARK_D2_OUTPUT`, default: `png`),
  `--d2-bundle-remote` (`MARK_D2_BUNDLE_REMOTE`),
  `--d2-scale N` (`MARK_D2_SCALE`, default: `1.0`) — D2 rendering.

Unsupported `Folder`/`Property`/`Order`/`Include` file headers likewise
warn per file and change nothing.

## Examples

```sh
# Preview the generated storage HTML (no credentials needed)
mark sync --files README.md --compile-only --title-from-h1

# See what would change, without writing anything
mark sync --files "docs/**/*.md" --space DOC --base-url https://tenant.atlassian.net/wiki \
  --username me@example.com --password <api-token> --dry-run

# Publish one page directly by URL
mark sync --files page.md --target-url \
  'https://tenant.atlassian.net/wiki/pages/viewpage.action?pageId=123' \
  --username me@example.com --password <api-token>

# Mirror a docs tree, deriving parents from directories
mark sync --files "docs/**/*.md" --space DOC --parents-from-path \
  --base-url https://tenant.atlassian.net/wiki \
  --username me@example.com --password <api-token>

# CI: JSON report, keep going on errors, tolerate empty matches
mark sync --files "docs/**/*.md" --space DOC --output-format json \
  --continue-on-error --ci --base-url https://tenant.atlassian.net/wiki \
  --username me@example.com --password <api-token>
```

## Layout

- `pyproject.toml` — project metadata, `mark` console script (no dependencies)
- `mark/config.py` — TOML config file, `MARK_*` env, CLI merging, password
  resolution, option validation
- `mark/metadata.py` — HTML headers + YAML front matter parsing, title derivation
- `mark/converter.py` — Markdown to Confluence storage format (code macros,
  tables, lists, local images as attachments, raw-HTML passthrough)
- `mark/confluence.py` — Confluence v1 client (Basic/Bearer auth, page
  find/create/update with 409 retry, emoji + appearance properties,
  attachments with sha256 skip-if-identical, labels, 429/503 retries)
- `mark/cli.py` — `sync` command: target-url mode, parents-from-path,
  parent-chain creation, create/update/skip, `--changes-only` hashes,
  attachments, labels, `--compile-only`/`--dry-run`, `--output-format`
- `mark-research/` — original Go implementation (reference only, untouched)

## Scope notes

- Confluence Server/Data Center and Cloud v1 API only; `api.atlassian.com`
  gateway (scoped tokens, v2 API) fails fast with an explanatory error.
- Unchanged pages are skipped even without `--changes-only` (see above).
- Front matter is always parsed (upstream gates it on `--features`).
- Accepted-but-unimplemented flags warn at runtime and change nothing
  (see "Parameters").
- There is no watch mode: upstream mark has none either, so there is
  nothing to be par with. Re-run `mark sync` on change (e.g. via
  `watchexec`, `entr`, or a file-watching task) instead.
- Out of scope for now: `Include` expansion, cross-file page links,
  mermaid/d2/math rendering, mentions, folders, content properties,
  page moves/ordering, orphan handling, page-manifest tracking.
